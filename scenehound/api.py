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
from scenehound.matcher import MatchScore, score
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.observe import NULL_RECORDER, SessionStore
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
    store: SessionStore | None = None


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
    score: MatchScore

    @property
    def confidence(self) -> int:
        return self.score.confidence


def _best_scene(
    index: WantedIndex, title: str, threshold: int, skew: int
) -> tuple[SceneFingerprint, MatchScore] | None:
    """The scene to rewrite `title` to, else the closest wanted scene.

    At/above threshold: highest confidence, FIRST on ties (candidates come back
    ascending by scene_id) -- the RSS rewrite rule, unchanged. Below it: highest
    confidence, then most strong signals (vetoes zero the confidence, so this
    tells a vetoed near-miss from an unrelated scene), then first. None when no
    wanted scene shares a date or name token with the title.
    """
    hit: tuple[SceneFingerprint, MatchScore] | None = None
    near: tuple[SceneFingerprint, MatchScore] | None = None
    for scene in index.candidates_for_title(title):
        s = score(scene, title, other_sites=index.other_sites_for(scene),
                  date_skew_days=skew)
        if s.confidence >= threshold:
            if hit is None or s.confidence > hit[1].confidence:
                hit = (scene, s)
        elif near is None or (s.confidence, len(s.strong_signals)) > (
                near[1].confidence, len(near[1].strong_signals)):
            near = (scene, s)
    return hit or near


def _record_passthrough(state: AppState, results: list[ReleaseCandidate], rec) -> None:
    """Score passthrough results for the UI only. The response never depends
    on this, so any failure is logged and the rows skipped, never raised."""
    try:
        index = state.index_holder.current
        threshold = state.config.matching.threshold
        skew = state.config.matching.date_skew_days
        rows = []
        for c in results:
            best = (_best_scene(index, c.title, threshold, skew)
                    if index is not None else None)
            rows.append((c, *best) if best is not None else (c, None, None))
        rec.passed_through(rows)
    except Exception:
        log.exception("passthrough scoring for the UI failed (ignored)")


async def _passthrough(
    state: AppState, indexer: IndexerConfig, query: str, cats: tuple[int, ...], rec
) -> Response:
    bucket = state.buckets[indexer.slug]
    if not bucket.try_acquire():
        rec.note("rate-deferred: returned empty feed without querying Prowlarr")
        rec.passthrough_results(0)
        log.info("search slug=%s decision=rate-deferred q=%r", indexer.slug, query)
        return _xml(build_feed([]))
    results = await state.prowlarr.search(indexer.prowlarr_id, query, cats)
    rec.passthrough_results(len(results))
    # Recorded so a Whisparr grab of one correlates. Scoring is new work on
    # this path, so it only runs when there is a UI to show it.
    if state.store is not None:
        _record_passthrough(state, results, rec)
    log.info("search slug=%s mode=passthrough q=%r results=%d",
             indexer.slug, query, len(results))
    return _xml(build_feed([FeedEntry(c) for c in results]))


async def _search_mode(
    state: AppState, indexer: IndexerConfig, q: str, cats: tuple[int, ...], rec
) -> Response:
    index = state.index_holder.current
    parsed = parse_query_term(q)
    if index is None or parsed is None:
        rec.query(parsed, ())
        rec.fallback("unparseable-query" if parsed is None else "no-index")
        if parsed is None:
            log.warning("search slug=%s unparseable q=%r -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats, rec)

    scenes = index.resolve(parsed.site_token, list(parsed.dates))
    rec.query(parsed, scenes)
    if not scenes:
        rec.fallback("scene-unresolved")
        log.info("search slug=%s q=%r scene=unresolved -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats, rec)

    threshold = state.config.matching.threshold
    skew = state.config.matching.date_skew_days
    suffix = state.config.naming.original_title_suffix
    bucket = state.buckets[indexer.slug]
    best: dict[str, _Scored] = {}
    variants = plan_queries(scenes[0], state.config.matching.max_queries_per_search)
    rec.variants_planned(variants)
    fired = 0
    try:
        async with asyncio.timeout(TIME_BUDGET_SECONDS):
            for variant in variants:
                if not bucket.try_acquire():
                    rec.note(f"rate-deferred after {fired} queries")
                    log.info("search slug=%s decision=rate-deferred after=%d", indexer.slug, fired)
                    break
                fired += 1
                candidates = await state.prowlarr.search(indexer.prowlarr_id, variant, cats)
                rec.variant_fired(variant, len(candidates))
                for c in candidates:
                    for scene in scenes:
                        s = score(scene, c.title, other_sites=index.other_sites_for(scene),
                                  date_skew_days=skew)
                        log.debug(
                            "score slug=%s scene=%d title=%r conf=%d strong=%s veto=%s",
                            indexer.slug, scene.scene_id, c.title,
                            s.confidence, s.strong_signals, s.veto,
                        )
                        prev = best.get(c.guid)
                        if prev is None or s.confidence > prev.confidence:
                            best[c.guid] = _Scored(c, scene, s)
                if any(v.confidence >= threshold for v in best.values()):
                    break
    except TimeoutError:
        rec.note(f"time budget expired after {fired} queries")
        log.warning("search slug=%s q=%r time budget expired after %d queries",
                    indexer.slug, q, fired)

    matched = sorted(
        (v for v in best.values() if v.confidence >= threshold),
        key=lambda v: -v.confidence,
    )
    rec.scored([
        (v.candidate, v.scene, v.score,
         rewrite_title(v.scene, v.candidate.title, include_original=suffix)
         if v.confidence >= threshold else None)
        for v in best.values()
    ])
    log.info(
        "search slug=%s q=%r scenes=%s variants_fired=%d candidates=%d matched=%d",
        indexer.slug, q, [s.scene_id for s in scenes], fired, len(best), len(matched),
    )
    return _xml(build_feed([
        FeedEntry(v.candidate,
                  title_override=rewrite_title(v.scene, v.candidate.title,
                                               include_original=suffix))
        for v in matched
    ]))


async def _rss_mode(
    state: AppState, indexer: IndexerConfig, cats: tuple[int, ...], rec
) -> Response:
    # One fetch, identical cost to status-quo RSS sync: not bucket-gated.
    candidates = await state.prowlarr.search(indexer.prowlarr_id, None, cats)
    index = state.index_holder.current
    threshold = state.config.matching.threshold
    skew = state.config.matching.date_skew_days
    suffix = state.config.naming.original_title_suffix
    entries: list[FeedEntry] = []
    rss_matched: list[tuple] = []
    passed: list[tuple] = []
    for c in candidates:
        best = (_best_scene(index, c.title, threshold, skew)
                if index is not None else None)
        if best is not None and best[1].confidence >= threshold:
            scene, ms = best
            new_title = rewrite_title(scene, c.title, include_original=suffix)
            entries.append(FeedEntry(c, title_override=new_title))
            rss_matched.append((c, scene, ms, new_title))
            log.info(
                "rss slug=%s matched scene=%d conf=%d original=%r",
                indexer.slug, scene.scene_id, ms.confidence, c.title,
            )
        else:
            # Returned unchanged; recorded with its closest wanted scene so a
            # Whisparr grab of it correlates and shows why it wasn't rewritten.
            entries.append(FeedEntry(c))
            passed.append((c, *best) if best is not None else (c, None, None))
    if index is None:
        rec.note("wanted list not loaded — items passed through unscored")
    rec.passed_through(passed)
    rec.rss_summary(len(candidates), rss_matched)
    log.info("rss slug=%s items=%d rewritten=%d",
             indexer.slug, len(candidates), len(rss_matched))
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
    rec = (state.store.recorder(slug, state.config.matching.threshold, q)
           if state.store is not None else NULL_RECORDER)
    try:
        if q:
            return await _search_mode(state, indexer, q, cats, rec)
        return await _rss_mode(state, indexer, cats, rec)
    except ProwlarrError as exc:
        rec.error(str(exc))
        log.error("search slug=%s prowlarr error: %s", slug, exc)
        return _xml(build_error(900, str(exc)))
    except Exception as exc:
        rec.error(f"internal error: {exc}")
        raise
    finally:
        rec.commit()


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
