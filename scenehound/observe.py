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
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from scenehound.models import SceneFingerprint

log = logging.getLogger("scenehound.observe")

_UNMATCHED_GRABS_MAX = 20
# apikey-style query params inside GUIDs (which are sometimes URLs). Titles are
# release names, never URLs, and are stored verbatim so grab correlation can
# exact-match them.
_SECRET_KEYS = r"apikey|api_key|passkey|token|authkey|torrent_pass"
# authkey/torrent_pass: Gazelle trackers (empornium, happyfappy) put both in
# the download URL that Prowlarr hands us as the guid.
_SECRET_PARAM = re.compile(rf"(?i)\b({_SECRET_KEYS})=[^&\s]+")
# A secret param whose value isn't REDACTED yet. load() runs this over the raw
# JSON text, where _SECRET_PARAM's value class would run on past the closing
# quote and make an already-scrubbed file look dirty on every restart.
_UNREDACTED = re.compile(rf'(?i)\b(?:{_SECRET_KEYS})=(?!REDACTED\b)[^&\s"]')
# The separator rewriter.rewrite_title puts before the original tracker title:
# "<canonical> [<original>]". Duplicated here rather than imported to keep this
# module's no-imports-from-the-pipeline isolation rule; if the rewriter's
# separator ever changes, correlation quietly falls back to exact matching,
# which is where it started.
_ORIGINAL_SUFFIX_SEP = " ["


def _sanitize(text: str) -> str:
    return _SECRET_PARAM.sub(r"\1=REDACTED", text)


def _sanitize_opt(text: str | None) -> str | None:
    return _sanitize(text) if text else text


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
    # Identity tokens the matcher could not explain; populated for the
    # superset-title veto only. The UI names them so an over-firing veto is
    # visible — vetoed candidates never reach Whisparr, so /ui is the only
    # place a wrongly-rejected release can be seen.
    residual: tuple[str, ...] = ()


@dataclass(frozen=True)
class GrabEvent:
    release_title: str
    download_id: str
    at: float
    size: int | None = None     # webhook release.size; tie-breaker only


@dataclass(frozen=True)
class ImportEvent:
    at: float
    movie_id: int
    file_count: int
    dry_run: bool


@dataclass
class GrabRecord:
    # One grab correlated to this session. Mutable for the same reason as
    # Outcome: the import stamp arrives later than the grab.
    grab: GrabEvent
    # guid of the candidate this grab correlated to; None when ambiguous
    # (identical titles, size missing or unhelpful) — the UI then shows this
    # grab at session level only, with no row badge.
    grabbed_guid: str | None = None
    imported: ImportEvent | None = None


@dataclass
class Outcome:
    # Deliberately mutable: grabs are stamped AFTER the session commits
    # (webhook and import-completer arrive later). Everything else is frozen.
    status: str = "empty"            # matched | empty | error | rss-summary
    matched_count: int = 0
    items_total: int = 0             # RSS only
    rewritten: int = 0               # RSS only
    # One record per grab: several candidates of one session can each be
    # grabbed (and each import independently). Replaces the v0.2.0 single
    # grab/grabbed_guid/imported slots whose second grab overwrote the first.
    grabs: list[GrabRecord] = field(default_factory=list)


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


# ---- decoding the state file ------------------------------------------------
# snapshot() already emits exactly the shape written to disk, so only the way
# back needs code. Every field is read with a default: a state file written by
# an older or newer build must still load, minus whatever it didn't carry. A
# session whose shape is broken outright (wrong types, not just missing keys)
# raises here and is dropped individually by load().


def _scene_ref(d: dict) -> SceneRef:
    return SceneRef(
        scene_id=d.get("scene_id", 0), site=d.get("site", ""),
        date=d.get("date", ""), title=d.get("title", ""),
        performers=tuple(d.get("performers") or ()),
    )


def _variant(d: dict) -> VariantTrace:
    return VariantTrace(query=d.get("query", ""), fired=bool(d.get("fired")),
                        result_count=d.get("result_count"))


def _candidate(d: dict) -> CandidateTrace:
    return CandidateTrace(
        # Re-sanitized on the way in: files written before authkey and
        # torrent_pass were redacted still carry them.
        title=d.get("title", ""), guid=_sanitize(d.get("guid") or ""),
        size=d.get("size"), seeders=d.get("seeders"),
        scene_id=d.get("scene_id", 0), confidence=d.get("confidence", 0),
        strong_signals=tuple(d.get("strong_signals") or ()),
        veto=d.get("veto"), detail=dict(d.get("detail") or {}),
        matched=bool(d.get("matched")), rewritten_title=d.get("rewritten_title"),
        residual=tuple(d.get("residual") or ()),
    )


def _grab_event(d: dict) -> GrabEvent:
    return GrabEvent(release_title=d.get("release_title", ""),
                     download_id=d.get("download_id", ""),
                     at=d.get("at", 0.0), size=d.get("size"))


def _import_event(d: dict | None) -> ImportEvent | None:
    if not d:
        return None
    return ImportEvent(at=d.get("at", 0.0), movie_id=d.get("movie_id", 0),
                       file_count=d.get("file_count", 0),
                       dry_run=bool(d.get("dry_run")))


def _outcome(d: dict) -> Outcome:
    return Outcome(
        status=d.get("status", "empty"), matched_count=d.get("matched_count", 0),
        items_total=d.get("items_total", 0), rewritten=d.get("rewritten", 0),
        grabs=[GrabRecord(grab=_grab_event(g.get("grab") or {}),
                          # Scrubbed exactly like the candidate guid it points
                          # at, so the correlation key still agrees.
                          grabbed_guid=_sanitize_opt(g.get("grabbed_guid")),
                          imported=_import_event(g.get("imported")))
               for g in d.get("grabs") or ()],
    )


def _unmatched_grab(d: dict) -> UnmatchedGrab:
    return UnmatchedGrab(grab=_grab_event(d.get("grab") or {}),
                         imported=_import_event(d.get("imported")))


def _session(d: dict) -> SearchSession:
    return SearchSession(
        session_id=d.get("session_id", 0),
        started_at=d.get("started_at", 0.0), finished_at=d.get("finished_at", 0.0),
        slug=d.get("slug", ""), kind=d.get("kind", "search"),
        raw_query=d.get("raw_query", ""), threshold=d.get("threshold", 0),
        parsed_site=d.get("parsed_site"),
        parsed_dates=tuple(d.get("parsed_dates") or ()),
        scenes=tuple(_scene_ref(s) for s in d.get("scenes") or ()),
        variants=tuple(_variant(v) for v in d.get("variants") or ()),
        candidates=tuple(_candidate(c) for c in d.get("candidates") or ()),
        dropped_candidates=d.get("dropped_candidates", 0),
        outcome=_outcome(d.get("outcome") or {}),
        fallback_reason=d.get("fallback_reason"),
        notes=tuple(d.get("notes") or ()),
    )


class SessionStore:
    """Bounded, process-local ring of recent sessions. Newest first.

    save()/load() let that ring survive a restart; they do not change what it
    is. Both are shielded like everything else here: a corrupt, truncated, or
    unwritable state file must degrade to an empty UI, never to a failed
    startup or a failed search.
    """

    def __init__(self, max_sessions: int, max_candidates: int) -> None:
        self._sessions: deque = deque(maxlen=max_sessions)
        self._unmatched_grabs: deque = deque(maxlen=_UNMATCHED_GRABS_MAX)
        self._next = 0
        self._max_candidates = max_candidates
        self._dirty = False

    @property
    def max_candidates(self) -> int:
        return self._max_candidates

    def next_id(self) -> int:
        self._next += 1
        return self._next

    @_shielded
    def add(self, session: SearchSession) -> None:
        self._dirty = True
        self._sessions.appendleft(session)

    @_shielded
    def save(self, path: Path) -> None:
        """Write the ring to `path`, atomically, only if something changed."""
        if not self._dirty:
            return
        tmp = Path(path).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"next_id": self._next, **self.snapshot()}, fh)
            # Without the flush+fsync, os.replace can publish a file whose
            # contents never reached the disk — on a host power cut that is
            # exactly the empty file the atomic rename was meant to prevent.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        self._dirty = False

    @_shielded
    def load(self, path: Path) -> None:
        """Refill the ring from `path`. Absent or unreadable file: stay empty."""
        p = Path(path)
        if not p.exists():
            return
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        sessions = []
        for d in data.get("sessions") or ():
            try:
                sessions.append(_session(d))
            except Exception:
                log.exception("observe: dropped an undecodable stored session")
        grabs = []
        for d in data.get("unmatched_grabs") or ():
            try:
                grabs.append(_unmatched_grab(d))
            except Exception:
                log.exception("observe: dropped an undecodable stored grab")
        # Stored newest-first, so a shrunken max_sessions must cut the TAIL;
        # deque(maxlen=) fills from the left and would keep the oldest instead.
        cap = self._sessions.maxlen
        self._sessions = deque(sessions[:cap], maxlen=cap)
        self._unmatched_grabs = deque(grabs[:_UNMATCHED_GRABS_MAX],
                                      maxlen=_UNMATCHED_GRABS_MAX)
        # Never reissue an id a restored session already carries.
        self._next = max([data.get("next_id") or 0] + [s.session_id for s in sessions])
        # The decoders scrubbed any unredacted secret from memory; flag the
        # file so the next flush rewrites it too, instead of leaving the
        # secrets on disk until a search happens to dirty the store.
        self._dirty = _UNREDACTED.search(text) is not None
        log.info("ui state restored sessions=%d unmatched_grabs=%d from %s",
                 len(self._sessions), len(self._unmatched_grabs), p)

    def recorder(self, slug: str, threshold: int, raw_query: str) -> "Recorder":
        return Recorder(self, slug, threshold, raw_query)

    @staticmethod
    def _correlates(c: "CandidateTrace", release_title: str) -> bool:
        """Does this stored candidate correspond to the webhook's release title?

        Exact match on either side of the rewrite, plus the canonical PREFIX of
        the rewritten title. The prefix arm is a hedge, not a feature. Part A
        made `rewritten_title` "<canonical> [<original tracker title>]", and the
        parse contract behind that was verified against `GET /api/v3/parse` —
        not against the On Grab webhook, which is what actually feeds this
        method, and which CI cannot exercise. If Whisparr ever reports a title
        it re-derived rather than the one it was handed, exact equality stops
        correlating and the UI's ladder stalls at Matched with nothing logged as
        an error anywhere: silence is the failure mode. There is already one
        open "RSS grab not marked" investigation in this repo that a second
        silent correlation break would confuse badly.

        Accepting the prefix is free: it is a string this very session emitted
        pre-suffix, so it cannot correlate anything the bare canonical title did
        not already correlate before Part A existed.
        """
        if release_title in (c.title, c.rewritten_title):
            return True
        rw = c.rewritten_title
        return bool(rw) and rw.split(_ORIGINAL_SUFFIX_SEP, 1)[0] == release_title

    @staticmethod
    def _correlate_guid(matches, size: int | None) -> str | None:
        if len(matches) == 1:
            return matches[0].guid
        if size is not None:
            # Twin releases of one scene can rewrite to identical titles;
            # the webhook's size is what tells them apart.
            by_size = [c for c in matches if c.size == size]
            if len(by_size) == 1:
                return by_size[0].guid
        return None

    @_shielded
    def record_grab(self, release_title: str, download_id: str,
                    size: int | None = None) -> None:
        self._dirty = True
        ev = GrabEvent(release_title, download_id, time.time(), size)
        for s in self._sessions:  # deque is newest-first already
            matches = [c for c in s.candidates
                       if release_title and self._correlates(c, release_title)]
            if not matches:
                continue
            guid = self._correlate_guid(matches, size)
            log.info("grab correlated session=%d kind=%s slug=%s title=%r",
                     s.session_id, s.kind, s.slug, release_title)
            if download_id:
                # Webhook resend / re-grab of the same download: update the
                # existing record in place (keeping any import stamp it
                # already earned) rather than appending a duplicate.
                for rec in s.outcome.grabs:
                    if rec.grab.download_id == download_id:
                        rec.grab = ev
                        rec.grabbed_guid = guid
                        return
            s.outcome.grabs.append(GrabRecord(grab=ev, grabbed_guid=guid))
            return
        log.warning("grab uncorrelated: no stored session lists title=%r "
                    "(download_id=%s) — surfacing in unmatched grabs",
                    release_title, download_id or "?")
        self._unmatched_grabs.appendleft(UnmatchedGrab(ev))

    @_shielded
    def record_import(self, download_id: str, movie_id: int,
                      file_count: int, dry_run: bool) -> None:
        self._dirty = True
        ev = ImportEvent(time.time(), movie_id, file_count, dry_run)
        if download_id:  # an id-less import can't be correlated to anything
            for s in self._sessions:
                for rec in s.outcome.grabs:
                    if rec.grab.download_id == download_id:
                        rec.imported = ev
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
                residual=ms.residual,
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
