"""Process-local observability for the web UI: bounded session store + traces.

Isolation rules (the whole point of this module):
- api.py imports observe; observe imports NOTHING from api.py,
  import_completer.py, or FastAPI. Pure data + bookkeeping.
- No public method of SessionStore or Recorder ever raises: an observability
  bug degrades to a missing/partial UI entry, never a broken search or grab.
- Single-writer assumption: every caller runs on the one asyncio event loop
  and no method awaits, so plain deques/dicts need no locking.
"""
from __future__ import annotations

import dataclasses
import functools
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

from scenehound.models import SceneFingerprint

log = logging.getLogger("scenehound.observe")

_UNMATCHED_GRABS_MAX = 20
# apikey-style query params inside GUIDs (which are sometimes URLs). Titles are
# release names, never URLs, and are stored verbatim so grab correlation can
# exact-match them.
_SECRET_PARAM = re.compile(r"(?i)\b(apikey|api_key|passkey|token)=[^&\s]+")


def _sanitize(text: str) -> str:
    return _SECRET_PARAM.sub(r"\1=REDACTED", text)


def _shielded(fn):
    """Observability must never break the caller: swallow and log everything."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("observe: %s failed (ignored)", fn.__name__)
            return None
    return wrapper


@dataclass(frozen=True)
class SceneRef:
    scene_id: int
    site: str
    date: str  # ISO
    title: str
    performers: tuple[str, ...]

    @classmethod
    def from_scene(cls, s: SceneFingerprint) -> "SceneRef":
        return cls(s.scene_id, s.site, s.date.isoformat(), s.title, s.performers)


@dataclass(frozen=True)
class VariantTrace:
    query: str
    fired: bool
    result_count: int | None


@dataclass(frozen=True)
class CandidateTrace:
    title: str                       # ORIGINAL release title from Prowlarr
    guid: str                        # sanitized; identity only, never displayed
    size: int | None
    seeders: int | None
    scene_id: int                    # best-matching scene
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]
    matched: bool
    rewritten_title: str | None      # what we returned to Whisparr, if matched


@dataclass(frozen=True)
class GrabEvent:
    release_title: str
    download_id: str
    at: float


@dataclass(frozen=True)
class ImportEvent:
    at: float
    movie_id: int
    file_count: int
    dry_run: bool


@dataclass
class Outcome:
    # Deliberately mutable: grab/imported are stamped AFTER the session commits
    # (webhook and import-completer arrive later). Everything else is frozen.
    status: str = "empty"            # matched | empty | error | rss-summary
    matched_count: int = 0
    items_total: int = 0             # RSS only
    rewritten: int = 0               # RSS only
    grab: GrabEvent | None = None
    imported: ImportEvent | None = None


@dataclass
class UnmatchedGrab:
    # A grab (or import) we couldn't correlate to a stored session — surfaced
    # in the UI rather than silently dropped. Mutable for the same reason.
    grab: GrabEvent
    imported: ImportEvent | None = None


@dataclass(frozen=True)
class SearchSession:
    session_id: int
    started_at: float
    finished_at: float
    slug: str
    kind: str                        # search | passthrough | rss
    raw_query: str                   # "" for RSS
    threshold: int                   # matching threshold AT CAPTURE TIME
    parsed_site: str | None
    parsed_dates: tuple[str, ...]    # ISO
    scenes: tuple[SceneRef, ...]
    variants: tuple[VariantTrace, ...]
    candidates: tuple[CandidateTrace, ...]  # confidence desc, capped
    dropped_candidates: int
    outcome: Outcome
    fallback_reason: str | None      # unparseable-query | scene-unresolved | no-index
    notes: tuple[str, ...]


class SessionStore:
    """Bounded, process-local ring of recent sessions. Newest first."""

    def __init__(self, max_sessions: int, max_candidates: int) -> None:
        self._sessions: deque = deque(maxlen=max_sessions)
        self._unmatched_grabs: deque = deque(maxlen=_UNMATCHED_GRABS_MAX)
        self._next = 0
        self._max_candidates = max_candidates

    @property
    def max_candidates(self) -> int:
        return self._max_candidates

    def next_id(self) -> int:
        self._next += 1
        return self._next

    @_shielded
    def add(self, session: SearchSession) -> None:
        self._sessions.appendleft(session)

    def snapshot(self) -> dict:
        def _to_json_safe(obj):
            """Recursively convert tuples to lists for JSON serialization."""
            if isinstance(obj, dict):
                return {k: _to_json_safe(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [_to_json_safe(item) for item in obj]
            else:
                return obj

        sessions = []
        for s in self._sessions:
            try:
                sessions.append(_to_json_safe(dataclasses.asdict(s)))
            except Exception:
                log.exception("observe: snapshot skipped a bad session")
        grabs = []
        for u in self._unmatched_grabs:
            try:
                grabs.append(_to_json_safe(dataclasses.asdict(u)))
            except Exception:
                log.exception("observe: snapshot skipped a bad grab")
        return {"sessions": sessions, "unmatched_grabs": grabs}
