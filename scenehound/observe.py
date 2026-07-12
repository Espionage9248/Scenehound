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

    def recorder(self, slug: str, threshold: int, raw_query: str) -> "Recorder":
        return Recorder(self, slug, threshold, raw_query)

    @_shielded
    def record_grab(self, release_title: str, download_id: str) -> None:
        ev = GrabEvent(release_title, download_id, time.time())
        for s in self._sessions:  # deque is newest-first already
            for c in s.candidates:
                if release_title and (c.rewritten_title == release_title
                                      or c.title == release_title):
                    s.outcome.grab = ev
                    return
        self._unmatched_grabs.appendleft(UnmatchedGrab(ev))

    @_shielded
    def record_import(self, download_id: str, movie_id: int,
                      file_count: int, dry_run: bool) -> None:
        ev = ImportEvent(time.time(), movie_id, file_count, dry_run)
        for s in self._sessions:
            grab = s.outcome.grab
            if grab is not None and grab.download_id == download_id:
                s.outcome.imported = ev
                return
        for u in self._unmatched_grabs:
            if u.grab.download_id == download_id:
                u.imported = ev
                return
        # An import for a grab we never saw (e.g. UI enabled mid-flight):
        # surface it rather than drop it.
        self._unmatched_grabs.appendleft(
            UnmatchedGrab(GrabEvent("", download_id, ev.at), imported=ev))

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


class Recorder:
    """Accumulates one request's trace; commit() files it exactly once.

    Every public method is _shielded: the search path calls these inline, so
    they must be incapable of raising.
    """

    def __init__(self, store: SessionStore, slug: str, threshold: int, raw_query: str) -> None:
        self._store = store
        self._slug = slug
        self._threshold = threshold
        self._raw_query = raw_query
        self._started = time.time()
        self._kind = "search" if raw_query else "rss"
        self._parsed_site: str | None = None
        self._parsed_dates: tuple[str, ...] = ()
        self._scenes: tuple[SceneRef, ...] = ()
        self._planned: list[str] = []
        self._fired: dict[str, int] = {}      # query -> result_count (insertion-ordered)
        self._cands: list[CandidateTrace] = []
        self._fallback: str | None = None
        self._notes: list[str] = []
        self._error: str | None = None
        self._items_total = 0
        self._rewritten = 0
        self._passthrough_count: int | None = None
        self._committed = False

    @_shielded
    def query(self, parsed, scenes) -> None:
        if parsed is not None:
            self._parsed_site = parsed.site_token
            self._parsed_dates = tuple(d.isoformat() for d in parsed.dates)
        self._scenes = tuple(SceneRef.from_scene(s) for s in scenes)

    @_shielded
    def fallback(self, reason: str) -> None:
        self._kind = "passthrough"
        self._fallback = reason

    @_shielded
    def variants_planned(self, queries) -> None:
        self._planned = list(queries)

    @_shielded
    def variant_fired(self, query: str, result_count: int) -> None:
        self._fired[query] = result_count

    @_shielded
    def note(self, text: str) -> None:
        self._notes.append(text)

    @_shielded
    def scored(self, items) -> None:
        # items: iterable of (ReleaseCandidate, SceneFingerprint, MatchScore,
        # rewritten_title | None). URLs (link/enclosure) are deliberately never
        # read: they embed the Prowlarr API key.
        for cand, scene, ms, rewritten in items:
            self._cands.append(CandidateTrace(
                title=cand.title,
                guid=_sanitize(cand.guid),
                size=cand.size,
                seeders=cand.seeders,
                scene_id=scene.scene_id,
                confidence=ms.confidence,
                strong_signals=ms.strong_signals,
                veto=ms.veto,
                detail=dict(ms.detail),
                matched=ms.confidence >= self._threshold,
                rewritten_title=rewritten,
            ))

    @_shielded
    def passthrough_results(self, count: int) -> None:
        self._kind = "passthrough"
        self._passthrough_count = count

    @_shielded
    def rss_summary(self, items_total: int, matched) -> None:
        self._kind = "rss"
        self._items_total = items_total
        self.scored(matched)
        self._rewritten = len(self._cands)

    @_shielded
    def error(self, text: str) -> None:
        self._error = text

    @_shielded
    def commit(self) -> None:
        if self._committed:
            return
        self._committed = True
        cands = sorted(self._cands, key=lambda c: -c.confidence)
        cap = self._store.max_candidates
        dropped = 0
        if len(cands) > cap:
            # Matched candidates always survive the cap; non-matched fill the rest.
            keep = [c for c in cands if c.matched]
            keep += [c for c in cands if not c.matched][: max(0, cap - len(keep))]
            dropped = len(cands) - len(keep)
            cands = sorted(keep, key=lambda c: -c.confidence)
        matched_count = sum(1 for c in cands if c.matched)
        variants = tuple(
            [VariantTrace(q, True, n) for q, n in self._fired.items()]
            + [VariantTrace(q, False, None) for q in self._planned if q not in self._fired]
        )
        notes = list(self._notes)
        if self._error is not None:
            status = "error"
            notes.append(self._error)
        elif self._kind == "rss":
            status = "rss-summary"
        elif self._kind == "passthrough":
            # Passthrough returns Prowlarr's results verbatim; "matched" here
            # means "returned something", per the spec.
            matched_count = self._passthrough_count or 0
            status = "matched" if matched_count else "empty"
        else:
            status = "matched" if matched_count else "empty"
        self._store.add(SearchSession(
            session_id=self._store.next_id(),
            started_at=self._started,
            finished_at=time.time(),
            slug=self._slug,
            kind=self._kind,
            raw_query=self._raw_query,
            threshold=self._threshold,
            parsed_site=self._parsed_site,
            parsed_dates=self._parsed_dates,
            scenes=self._scenes,
            variants=variants,
            candidates=tuple(cands),
            dropped_candidates=dropped,
            outcome=Outcome(status=status, matched_count=matched_count,
                            items_total=self._items_total, rewritten=self._rewritten),
            fallback_reason=self._fallback,
            notes=tuple(notes),
        ))


class NullRecorder:
    """Shared no-op stand-in when the UI is disabled: zero work, zero state."""

    def query(self, parsed, scenes) -> None: ...
    def fallback(self, reason) -> None: ...
    def variants_planned(self, queries) -> None: ...
    def variant_fired(self, query, result_count) -> None: ...
    def note(self, text) -> None: ...
    def scored(self, items) -> None: ...
    def passthrough_results(self, count) -> None: ...
    def rss_summary(self, items_total, matched) -> None: ...
    def error(self, text) -> None: ...
    def commit(self) -> None: ...


NULL_RECORDER = NullRecorder()
