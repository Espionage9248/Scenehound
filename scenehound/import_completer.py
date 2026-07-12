"""Import-completer: auto-triggers Whisparr's held by-ID manual imports.

Opt-in and dry-run by default; fully isolated from the Torznab search path.
This module holds the *brain* — parsed Whisparr record types and PURE decision
functions — plus the stateful ImportCompleter service. No FastAPI import lives
here; the webhook route is in import_api.py.

Decision purity: plan_phase1 / match_pack / finalize_pack take already-fetched
data and return an ActionPlan or a Skip(reason). The service performs all I/O
around them, so decisions are tested against fixtures with no HTTP.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from scenehound.config import ImportCompleterConfig

log = logging.getLogger("scenehound.import_completer")

HELD_STATES = frozenset({"importBlocked", "importPending"})
# The proven by-ID hold. Matched case-insensitively as substrings so minor
# wording drift across eros versions fails SAFE (no marker -> not handled).
_BY_ID_MARKERS = ("matched to movie by id", "manual import required")


@dataclass(frozen=True)
class QueueItem:
    download_id: str
    movie_id: int
    tracked_state: str
    status_messages: tuple[str, ...]
    title: str


def queue_item_from_record(record: dict) -> QueueItem | None:
    download_id = record.get("downloadId")
    movie_id = record.get("movieId")
    if not download_id or not movie_id:
        return None
    msgs: list[str] = []
    for block in record.get("statusMessages", []) or []:
        if isinstance(block, dict):
            if title := block.get("title"):
                msgs.append(str(title))
            for m in block.get("messages", []) or []:
                msgs.append(str(m))
        elif isinstance(block, str):
            msgs.append(block)
    return QueueItem(
        download_id=str(download_id),
        movie_id=int(movie_id),
        tracked_state=str(record.get("trackedDownloadState", "")),
        status_messages=tuple(msgs),
        title=str(record.get("title", "")),
    )


@dataclass(frozen=True)
class ManualImportItem:
    path: str
    folder_name: str
    movie_id: int | None
    quality: dict | None
    languages: tuple[dict, ...]
    release_group: str | None
    rejections: tuple[str, ...]
    is_sample: bool
    size: int
    # The transient manualimport-session id and indexer flags. Whisparr's own UI
    # threads these back into the ManualImport command; we mirror it. Import still
    # works from path+movieId if the id is ignored/stale, so it is belt-and-braces.
    item_id: int | None = None
    indexer_flags: int = 0
    # The candidate's embedded movie carries monitored + movieFileId (NOT hasFile;
    # this eros fork exposes movieFileId, 0 == no file). Phase 2 reads these to
    # avoid an extra /movie/{id} fetch when the movie is pre-populated.
    monitored: bool | None = None
    movie_file_id: int | None = None


def _rejection_strings(raw: list) -> tuple[str, ...]:
    out: list[str] = []
    for r in raw or []:
        if isinstance(r, dict):
            out.append(str(r.get("reason", r)))
        else:
            out.append(str(r))
    return tuple(out)


def manual_import_from_record(record: dict) -> ManualImportItem:
    movie = record.get("movie") if isinstance(record.get("movie"), dict) else None
    movie_id = int(movie["id"]) if movie and movie.get("id") else None
    rejections = _rejection_strings(record.get("rejections", []))
    is_sample = any("sample" in r.lower() for r in rejections)
    langs = tuple(l for l in (record.get("languages") or []) if isinstance(l, dict))
    item_id = int(record["id"]) if record.get("id") is not None else None
    return ManualImportItem(
        path=str(record.get("path", "")),
        folder_name=str(record.get("folderName", "")),
        movie_id=movie_id,
        quality=record.get("quality"),
        languages=langs,
        release_group=record.get("releaseGroup"),
        rejections=rejections,
        is_sample=is_sample,
        size=int(record.get("size", 0)),
        item_id=item_id,
        indexer_flags=int(record.get("indexerFlags", 0) or 0),
        monitored=bool(movie["monitored"]) if movie and "monitored" in movie else None,
        movie_file_id=int(movie["movieFileId"]) if movie and movie.get("movieFileId") is not None else None,
    )


def is_by_id_hold(item: QueueItem) -> bool:
    if item.tracked_state not in HELD_STATES:
        return False
    haystack = " ".join(item.status_messages).lower()
    return any(marker in haystack for marker in _BY_ID_MARKERS)


@dataclass(frozen=True)
class ActionPlan:
    files: tuple[dict, ...]


@dataclass(frozen=True)
class Skip:
    reason: str


def _file_entry(path: str, movie_id: int, cand: ManualImportItem, download_id: str) -> dict:
    # Quality / languages / releaseGroup are taken VERBATIM from Whisparr's own
    # candidate parse — we never second-guess the file. downloadId associates the
    # import with the tracked download so it clears and downstream import events fire.
    # id/indexerFlags mirror what Whisparr's UI threads back into the command.
    entry = {
        "path": path,
        "movieId": movie_id,
        "quality": cand.quality,
        "languages": list(cand.languages),
        "releaseGroup": cand.release_group,
        "downloadId": download_id,
        "indexerFlags": cand.indexer_flags,
    }
    if cand.item_id is not None:
        entry["id"] = cand.item_id
    return entry


def plan_phase1(
    item: QueueItem, candidates: list[ManualImportItem], config: ImportCompleterConfig
) -> ActionPlan | Skip:
    videos = [c for c in candidates if not c.is_sample]
    if len(videos) != 1:
        return Skip(f"not-single-video-file (videos={len(videos)})")
    cand = videos[0]
    if cand.movie_id is None:
        return Skip("candidate-has-no-movie (Whisparr did not pre-populate a by-ID match)")
    if cand.movie_id != item.movie_id:
        return Skip(f"movie-id-mismatch (candidate={cand.movie_id} grabbed={item.movie_id})")
    if cand.rejections:
        return Skip(f"has-rejections {list(cand.rejections)}")
    return ActionPlan(files=(_file_entry(cand.path, cand.movie_id, cand, item.download_id),))


@dataclass
class SweepSummary:
    acted: int = 0
    skipped: int = 0
    waited: int = 0
    parked: int = 0


class ImportCompleter:
    """Doorbell-driven sweep of Whisparr's queue for held by-ID imports.

    State is process-local: first-seen timestamps (grace), attempt counts +
    parked set (bounded retry), and a last-logged-skip map (quiet logs). A
    successful import removes the item from the queue, so duplicate wakes are
    naturally no-ops. Import errors from Whisparr are logged and re-tried on the
    next sweep (bounded by max_attempts), never raised out of the loop.
    """

    def __init__(self, client, index_holder, config: ImportCompleterConfig) -> None:
        self._client = client
        self._index_holder = index_holder
        self._config = config
        self._wake = asyncio.Event()
        self._first_seen: dict[str, float] = {}
        self._attempts: dict[str, int] = {}
        self._parked: set[str] = set()
        self._logged_skips: dict[str, str] = {}

    def notify(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        await self._safe_sweep(loop.time())  # startup sweep covers missed webhooks
        while True:
            timeout = self._next_timeout(loop.time())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._wake.clear()
            await self._safe_sweep(loop.time())

    def _next_timeout(self, now: float) -> float:
        # Wake at the earliest pending grace expiry, else the reconcile interval.
        pending = [
            self._first_seen[d] + self._config.grace_seconds - now
            for d in self._first_seen
            if d not in self._parked
            and self._first_seen[d] + self._config.grace_seconds > now
        ]
        if pending:
            return max(0.5, min(self._config.reconcile_seconds, min(pending)))
        return self._config.reconcile_seconds

    async def _safe_sweep(self, now: float) -> SweepSummary:
        try:
            return await self.sweep(now)
        except Exception as exc:  # never let the loop die on a transient Whisparr error
            log.error("import sweep failed (will retry): %s", exc)
            return SweepSummary()

    async def sweep(self, now: float) -> SweepSummary:
        s = SweepSummary()
        records = await self._client.fetch_queue()
        for record in records:
            item = queue_item_from_record(record)
            if item is None or not is_by_id_hold(item):
                continue
            did = item.download_id
            if did in self._parked:
                continue
            self._first_seen.setdefault(did, now)
            if now - self._first_seen[did] < self._config.grace_seconds:
                s.waited += 1
                continue
            attempts = self._attempts.get(did, 0)
            if attempts >= self._config.max_attempts:
                self._parked.add(did)
                log.warning("import parked download_id=%s after %d attempts", did, attempts)
                s.parked += 1
                continue
            plan = await self._plan(item)
            if isinstance(plan, Skip):
                if self._logged_skips.get(did) != plan.reason:
                    self._logged_skips[did] = plan.reason
                    log.info("import skip download_id=%s reason=%s", did, plan.reason)
                s.skipped += 1
                continue
            self._attempts[did] = attempts + 1
            if self._config.dry_run:
                log.info(
                    "DRY-RUN import download_id=%s files=%d body=%s",
                    did, len(plan.files),
                    {"name": "ManualImport", "importMode": "copy", "files": list(plan.files)},
                )
            else:
                await self._client.post_manual_import(list(plan.files))
                log.info("import fired download_id=%s files=%d movie=%d",
                         did, len(plan.files), item.movie_id)
            s.acted += 1
        return s

    async def _plan(self, item: QueueItem) -> "ActionPlan | Skip":
        cand_records = await self._client.fetch_manual_import(item.download_id)
        candidates = [manual_import_from_record(c) for c in cand_records]
        videos = [c for c in candidates if not c.is_sample]
        if len(videos) <= 1:
            return plan_phase1(item, candidates, self._config)
        if not self._config.multipack:
            return Skip(f"multipack-disabled (videos={len(videos)})")
        return await self._plan_phase2(item, candidates)

    async def _plan_phase2(
        self, item: QueueItem, candidates: list[ManualImportItem]
    ) -> "ActionPlan | Skip":
        return Skip("phase2-not-implemented")  # replaced in Task 7
