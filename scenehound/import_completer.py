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
import os
from dataclasses import dataclass

from scenehound.config import ImportCompleterConfig
from scenehound.matcher import score

log = logging.getLogger("scenehound.import_completer")

HELD_STATES = frozenset({"importBlocked", "importPending"})
# The proven by-ID hold. Matched case-insensitively as a substring so minor
# wording drift across eros versions fails SAFE (no marker -> not handled). Only
# the DISTINCTIVE "matched to movie by id" phrase qualifies; the generic "manual
# import required" appears in unrelated manual-import prompts and is not enough.
_BY_ID_MARKERS = ("matched to movie by id",)


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
    # EXACT "sample" only: Radarr-lineage also emits "Unable to determine if file
    # is a sample" for an INDETERMINATE file, which must NOT be excluded as a sample
    # (else a 2-file torrent could pass phase-1 as single and the real file is deleted).
    is_sample = any(r.strip().lower() == "sample" for r in rejections)
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


@dataclass(frozen=True)
class FileMatch:
    path: str
    cand: "ManualImportItem"
    movie_id: int | None
    verdict: str  # "matched" | "unmatched" | "ambiguous"


@dataclass(frozen=True)
class PackMatch:
    files: tuple[FileMatch, ...]

    @property
    def fully_matched(self) -> bool:
        # All-or-nothing: an empty pack or ANY non-matched file blocks the whole pack.
        return bool(self.files) and all(f.verdict == "matched" for f in self.files)

    @property
    def matched_movie_ids(self) -> frozenset[int]:
        return frozenset(f.movie_id for f in self.files if f.movie_id is not None)


def _match_one(
    cand: ManualImportItem, item: QueueItem, index, config: ImportCompleterConfig
) -> FileMatch:
    # Any rejection blocks the file (and, all-or-nothing, the whole pack) — phase 1
    # requires zero rejections, so a scorer- or ID-matched pack file with a rejection
    # ("Unknown quality", "Not an upgrade") must NOT import either.
    if cand.rejections:
        return FileMatch(cand.path, cand, None, "unmatched")
    # A movie Whisparr pre-populated is trusted ONLY when it equals the grabbed
    # movieId (never a foreign by-ID guess we didn't make).
    if cand.movie_id is not None:
        verdict = "matched" if cand.movie_id == item.movie_id else "unmatched"
        return FileMatch(cand.path, cand, cand.movie_id if verdict == "matched" else None, verdict)
    # Score the BASENAME, not the absolute path: leading dirs ("data", "torrents",
    # "library") are junk tokens that could fabricate a spurious site n-gram.
    name = os.path.basename(cand.path) or cand.path
    scored = []
    for scene in index.candidates_for_title(name):
        s = score(scene, name, other_sites=index.other_sites_for(scene))
        scored.append((s.confidence, bool(s.strong_signals), scene.scene_id))
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] < config.import_threshold or not scored[0][1]:
        return FileMatch(cand.path, cand, None, "unmatched")
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if scored[0][0] - runner_up < config.ambiguity_margin:
        return FileMatch(cand.path, cand, None, "ambiguous")
    return FileMatch(cand.path, cand, scored[0][2], "matched")  # scene_id == movieId


def match_pack(
    item: QueueItem, candidates: list[ManualImportItem], index, config: ImportCompleterConfig
) -> PackMatch:
    videos = [c for c in candidates if not c.is_sample]
    return PackMatch(tuple(_match_one(c, item, index, config) for c in videos))


def finalize_pack(
    item: QueueItem,
    pack: PackMatch,
    movie_states: dict[int, tuple[bool, bool]],
    config: ImportCompleterConfig,
) -> ActionPlan | Skip:
    # movie_states: movie_id -> (monitored, has_file). The has_file derivation
    # (movieFileId on this eros fork) happens in the SERVICE; this stays HTTP-free.
    if not pack.fully_matched:
        verdicts = {f.path: f.verdict for f in pack.files if f.verdict != "matched"}
        return Skip(f"pack-not-fully-matched {verdicts}")
    for mid in pack.matched_movie_ids:
        # Default to (not-monitored, has-file) so a missing state fails SAFE (skip).
        monitored, has_file = movie_states.get(mid, (False, True))
        if not monitored:
            return Skip(f"movie-not-monitored ({mid})")
        if has_file:
            return Skip(f"movie-hasfile-already ({mid})")
    # Two files aimed at the SAME movieId (e.g. 1080p + 2160p of one scene, or a
    # fork that pre-populated every pack file with the grabbed movie) -> Whisparr
    # imports one and the other is left, then deleted at cleanup. Never silently
    # discard; skip the whole pack for manual handling.
    targets = [f.movie_id for f in pack.files]
    if len(set(targets)) != len(targets):
        return Skip(f"duplicate-movie-target {sorted(targets)}")
    files = tuple(
        _file_entry(f.path, f.movie_id, f.cand, item.download_id)
        for f in pack.files
    )
    return ActionPlan(files=files)


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
        self._last_fired: dict[str, float] = {}
        self._logged_dryrun: set[str] = set()

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
            log.exception("import sweep failed (will retry): %s", exc)
            return SweepSummary()

    async def sweep(self, now: float) -> SweepSummary:
        s = SweepSummary()
        seen_ids: set[str] = set()
        records = await self._client.fetch_queue()
        for record in records:
            # Collect the infohash of EVERY record (not just by-ID ones) — an imported
            # item leaves the queue entirely, so anything absent below gets pruned.
            did_raw = record.get("downloadId") if isinstance(record, dict) else None
            if did_raw:
                seen_ids.add(str(did_raw))
            try:
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
                # Live-only retry cap: dry-run items never clear the queue, so counting
                # their "acts" would silently park items during the observation rung of
                # the rollout.
                if (not self._config.dry_run
                        and self._attempts.get(did, 0) >= self._config.max_attempts):
                    self._parked.add(did)
                    log.warning("import parked download_id=%s after %d attempts",
                                did, self._attempts.get(did, 0))
                    s.parked += 1
                    continue
                plan = await self._plan(item)
                if isinstance(plan, Skip):
                    if self._logged_skips.get(did) != plan.reason:
                        self._logged_skips[did] = plan.reason
                        log.info("import skip download_id=%s reason=%s", did, plan.reason)
                    s.skipped += 1
                    continue
                if self._config.dry_run:
                    if did not in self._logged_dryrun:
                        self._logged_dryrun.add(did)
                        log.info(
                            "DRY-RUN import download_id=%s files=%d body=%s",
                            did, len(plan.files),
                            {"name": "ManualImport", "importMode": "copy",
                             "files": list(plan.files)},
                        )
                    s.acted += 1
                    continue
                # Live: don't double-fire while a prior command is still importing (the
                # item stays queued until Whisparr's async copy finishes). Cooldown =
                # one grace window.  [F5]
                if now - self._last_fired.get(did, float("-inf")) < self._config.grace_seconds:
                    s.waited += 1
                    continue
                self._attempts[did] = self._attempts.get(did, 0) + 1
                self._last_fired[did] = now
                await self._client.post_manual_import(list(plan.files))
                log.info("import fired download_id=%s files=%d movie=%d",
                         did, len(plan.files), item.movie_id)
                s.acted += 1
            except Exception:  # one malformed record must not abort the sweep
                log.exception("import record failed download_id=%s", did_raw)
                continue
        # Prune process-local state to the queue we just saw so a cleared/re-grabbed
        # infohash starts fresh next time it appears.
        self._parked &= seen_ids
        self._logged_dryrun &= seen_ids
        for store in (self._first_seen, self._attempts, self._logged_skips, self._last_fired):
            for d in [d for d in store if d not in seen_ids]:
                del store[d]
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
        index = self._index_holder.current
        if index is None:
            return Skip("no-wanted-index")
        pack = match_pack(item, candidates, index, self._config)
        if not pack.fully_matched:
            # Log the per-file verdict table so the manual fallback is a checkbox job.
            table = [(f.path, f.verdict, f.movie_id) for f in pack.files]
            log.info("pack blocked download_id=%s verdicts=%s", item.download_id, table)
            return finalize_pack(item, pack, {}, self._config)
        # Reuse embedded movie state where Whisparr PRE-POPULATED it (cand.movie_id
        # is set) — no fetch needed. For scorer-matched movies (cand.movie_id is None,
        # we assigned scene_id), fetch each missing movie_id at most once. This eros
        # fork exposes movieFileId (0 == no file), NOT hasFile.
        # Reuse embedded state ONLY when BOTH monitored and movieFileId are present:
        # a MISSING embedded movieFileId (None) must not be read as "no file" — fall
        # through to fetch_movie(mid) for the belt-and-braces re-check.
        movie_states: dict[int, tuple[bool, bool]] = {
            f.movie_id: (bool(f.cand.monitored), bool(f.cand.movie_file_id))
            for f in pack.files
            if f.movie_id is not None and f.cand.movie_id is not None
            and f.cand.monitored is not None and f.cand.movie_file_id is not None
        }
        for mid in pack.matched_movie_ids:
            if mid in movie_states:
                continue
            movie = await self._client.fetch_movie(mid)
            has_file = bool(movie.get("hasFile")) or bool(movie.get("movieFileId"))
            movie_states[mid] = (bool(movie.get("monitored")), has_file)
        return finalize_pack(item, pack, movie_states, self._config)
