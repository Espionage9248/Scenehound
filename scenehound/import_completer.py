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


def _rejection_strings(raw: list) -> tuple[str, ...]:
    out: list[str] = []
    for r in raw or []:
        if isinstance(r, dict):
            out.append(str(r.get("reason", r)))
        else:
            out.append(str(r))
    return tuple(out)


def manual_import_from_record(record: dict) -> ManualImportItem:
    movie = record.get("movie") or None
    movie_id = int(movie["id"]) if isinstance(movie, dict) and movie.get("id") else None
    rejections = _rejection_strings(record.get("rejections", []))
    is_sample = any("sample" in r.lower() for r in rejections)
    langs = tuple(l for l in (record.get("languages") or []) if isinstance(l, dict))
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
    return {
        "path": path,
        "movieId": movie_id,
        "quality": cand.quality,
        "languages": list(cand.languages),
        "releaseGroup": cand.release_group,
        "downloadId": download_id,
    }


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
