"""HTTP surface and orchestration: search mode, RSS mode, passthrough."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response

from scenehound.clients.prowlarr import ProwlarrClient, ProwlarrError
from scenehound.config import Config, IndexerConfig
from scenehound.dates import parse_query_term
from scenehound.matcher import score
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.query_planner import plan_queries
from scenehound.rate_limiter import TokenBucket
from scenehound.rewriter import rewrite_title
from scenehound.torznab import FeedEntry, build_caps, build_error, build_feed
from scenehound.wanted_index import WantedIndex

log = logging.getLogger("scenehound.api")
router = APIRouter()

TIME_BUDGET_SECONDS = 45.0
_DEFAULT_CATS = (6000,)


class IndexHolder:
    def __init__(self) -> None:
        self.current: WantedIndex | None = None
        self.refreshed_at: float | None = None

    def set(self, index: WantedIndex) -> None:
        self.current = index
        self.refreshed_at = time.monotonic()


@dataclass
class AppState:
    config: Config
    prowlarr: ProwlarrClient
    index_holder: IndexHolder
    buckets: dict[str, TokenBucket]


def _xml(content: bytes) -> Response:
    return Response(content=content, media_type="application/xml")


def _cats(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return _DEFAULT_CATS
    out = tuple(int(c) for c in raw.split(",") if c.strip().isdigit())
    return out or _DEFAULT_CATS


@dataclass
class _Scored:
    candidate: ReleaseCandidate
    scene: SceneFingerprint
    confidence: int


async def _passthrough(
    state: AppState, indexer: IndexerConfig, query: str, cats: tuple[int, ...]
) -> Response:
    bucket = state.buckets[indexer.slug]
    if not bucket.try_acquire():
        log.info("search slug=%s decision=rate-deferred q=%r", indexer.slug, query)
        return _xml(build_feed([]))
    results = await state.prowlarr.search(indexer.prowlarr_id, query, cats)
    log.info("search slug=%s mode=passthrough q=%r results=%d",
             indexer.slug, query, len(results))
    return _xml(build_feed([FeedEntry(c) for c in results]))


async def _search_mode(
    state: AppState, indexer: IndexerConfig, q: str, cats: tuple[int, ...]
) -> Response:
    index = state.index_holder.current
    parsed = parse_query_term(q)
    if index is None or parsed is None:
        if parsed is None:
            log.warning("search slug=%s unparseable q=%r -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats)

    scenes = index.resolve(parsed.site_token, list(parsed.dates))
    if not scenes:
        log.info("search slug=%s q=%r scene=unresolved -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats)

    threshold = state.config.matching.threshold
    bucket = state.buckets[indexer.slug]
    best: dict[str, _Scored] = {}
    variants = plan_queries(scenes[0], state.config.matching.max_queries_per_search)
    fired = 0
    try:
        async with asyncio.timeout(TIME_BUDGET_SECONDS):
            for variant in variants:
                if not bucket.try_acquire():
                    log.info("search slug=%s decision=rate-deferred after=%d", indexer.slug, fired)
                    break
                fired += 1
                candidates = await state.prowlarr.search(indexer.prowlarr_id, variant, cats)
                for c in candidates:
                    for scene in scenes:
                        s = score(scene, c.title, other_sites=index.other_sites_for(scene))
                        log.debug(
                            "score slug=%s scene=%d title=%r conf=%d strong=%s veto=%s",
                            indexer.slug, scene.scene_id, c.title,
                            s.confidence, s.strong_signals, s.veto,
                        )
                        prev = best.get(c.guid)
                        if prev is None or s.confidence > prev.confidence:
                            best[c.guid] = _Scored(c, scene, s.confidence)
                if any(v.confidence >= threshold for v in best.values()):
                    break
    except TimeoutError:
        log.warning("search slug=%s q=%r time budget expired after %d queries",
                    indexer.slug, q, fired)

    matched = sorted(
        (v for v in best.values() if v.confidence >= threshold),
        key=lambda v: -v.confidence,
    )
    log.info(
        "search slug=%s q=%r scenes=%s variants_fired=%d candidates=%d matched=%d",
        indexer.slug, q, [s.scene_id for s in scenes], fired, len(best), len(matched),
    )
    return _xml(build_feed([
        FeedEntry(v.candidate, title_override=rewrite_title(v.scene, v.candidate.title))
        for v in matched
    ]))


async def _rss_mode(
    state: AppState, indexer: IndexerConfig, cats: tuple[int, ...]
) -> Response:
    # One fetch, identical cost to status-quo RSS sync: not bucket-gated.
    candidates = await state.prowlarr.search(indexer.prowlarr_id, None, cats)
    index = state.index_holder.current
    entries: list[FeedEntry] = []
    rewritten = 0
    for c in candidates:
        entry = FeedEntry(c)
        if index is not None:
            # Score ALL candidate scenes and rewrite to the BEST (highest-
            # confidence) scene at or above threshold, not the first one that
            # clears it (candidates come back ascending by scene_id).
            best_scene: SceneFingerprint | None = None
            best_conf = -1
            for scene in index.candidates_for_title(c.title):
                s = score(scene, c.title, other_sites=index.other_sites_for(scene))
                if s.confidence >= state.config.matching.threshold and s.confidence > best_conf:
                    best_conf = s.confidence
                    best_scene = scene
            if best_scene is not None:
                entry = FeedEntry(c, title_override=rewrite_title(best_scene, c.title))
                rewritten += 1
                log.info(
                    "rss slug=%s matched scene=%d conf=%d original=%r",
                    indexer.slug, best_scene.scene_id, best_conf, c.title,
                )
        entries.append(entry)
    log.info("rss slug=%s items=%d rewritten=%d", indexer.slug, len(candidates), rewritten)
    return _xml(build_feed(entries))


@router.get("/indexer/{slug}/api")
async def torznab_endpoint(slug: str, request: Request) -> Response:
    state: AppState = request.app.state.scenehound
    params = request.query_params
    if params.get("apikey") != state.config.api_key:
        return _xml(build_error(100, "Incorrect user credentials"))
    indexer = next((i for i in state.config.indexers if i.slug == slug), None)
    if indexer is None:
        return _xml(build_error(201, "Incorrect parameter"))
    t = params.get("t", "")
    if t == "caps":
        return _xml(build_caps())
    if t != "search":
        return _xml(build_error(203, f"Function not available: {t!r}"))
    cats = _cats(params.get("cat"))
    q = (params.get("q") or "").strip()
    try:
        if q:
            return await _search_mode(state, indexer, q, cats)
        return await _rss_mode(state, indexer, cats)
    except ProwlarrError as exc:
        log.error("search slug=%s prowlarr error: %s", slug, exc)
        return _xml(build_error(900, str(exc)))


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    state: AppState = request.app.state.scenehound
    index = state.index_holder.current
    age = (
        time.monotonic() - state.index_holder.refreshed_at
        if state.index_holder.refreshed_at is not None
        else None
    )
    return {
        "status": "ok",
        "index_size": len(index) if index is not None else 0,
        "index_age_seconds": age,
    }
