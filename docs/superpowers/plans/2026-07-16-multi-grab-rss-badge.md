# Same-Session Multi-Grab RSS Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Whisparr grabs several candidates out of one RSS session, every grab keeps its own badge, guid correlation, and import stamp — the second grab no longer overwrites the first.

**Architecture:** Replace `Outcome`'s three single-value slots (`grab`, `grabbed_guid`, `imported`) with `grabs: list[GrabRecord]`, each record bundling `{grab, grabbed_guid, imported}`. Correlation stays entirely in Python (`observe.py`); the UI only iterates. The store is a purely in-memory `deque` (no persistence), so the old fields are removed outright — no compat shim.

**Tech Stack:** Python 3.12 dataclasses (`scenehound/observe.py`), vanilla-JS single-file UI (`scenehound/static/ui.html`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-16-multi-grab-rss-badge-design.md`

## Global Constraints

- Branch `feat/multi-grab-rss-badge` off `main`. Open a PR at the end; do NOT merge (the user integrates).
- Run the FULL suite `.venv/bin/python -m pytest` before every commit. Baseline on main: **305 passed**.
- Conventional-commit prefixes; every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- `scenehound/matcher.py` and `scenehound/dates.py` are UNTOUCHED (they stay pure). This change is observe + UI only.
- observe.py invariants: no public `SessionStore`/`Recorder` method may raise (`@_shielded` on all mutators); single-writer asyncio assumption (no locking); `api.py` imports observe, never the reverse.
- UI invariant: all new JS reads guard `(o.grabs || [])`; a **single-grab session must render pixel-identical to today** (no `×1` suffix, same classes/labels).
- `tests/test_import_api.py` and `tests/test_import_completer.py` need NO changes: their fakes spy on the unchanged `record_grab`/`record_import` signatures and never inspect `Outcome` shape. Do not edit them.

---

### Task 1: `observe.py` multi-grab data model + correlation

**Files:**
- Modify: `scenehound/observe.py` (Outcome ~line 99–112, record_grab ~167–191, record_import ~193–209, the `from dataclasses import dataclass` import at line 19)
- Test: `tests/test_observe.py` (snapshot test lines 60–61; the grab/import block from line 250 to EOF)
- Test: `tests/test_ui_api.py` (line 60 only)

**Interfaces:**
- Consumes: existing `GrabEvent`, `ImportEvent`, `CandidateTrace`, `UnmatchedGrab` (all unchanged).
- Produces: `GrabRecord` dataclass with fields `grab: GrabEvent`, `grabbed_guid: str | None`, `imported: ImportEvent | None`; `Outcome.grabs: list[GrabRecord]` (fields `grab`/`grabbed_guid`/`imported` REMOVED from `Outcome`); `record_grab`/`record_import` keep their exact public signatures. Snapshot JSON: `session["outcome"]["grabs"]` is a list of `{"grab": {...}, "grabbed_guid": ..., "imported": ...}` dicts. Task 2's UI iterates exactly this shape.

- [ ] **Step 1: Rewrite the grab/import tests to the new shape (failing first)**

In `tests/test_observe.py`, make two edits.

**Edit A** — in `test_snapshot_is_json_serializable_and_complete`, replace the two lines

```python
    assert s["outcome"]["grab"] is None
    assert s["outcome"]["grabbed_guid"] is None
```

with:

```python
    assert s["outcome"]["grabs"] == []
```

**Edit B** — replace everything from `def test_record_grab_correlates_by_rewritten_title():` (line 250) to the END of the file with the block below. The helpers above it (`_store_with_matched_session`, `_cand`, `_ms`, `SCENE`) are unchanged; `_store_with_twin_titles` is preserved verbatim inside the block.

```python
def test_record_grab_correlates_by_rewritten_title():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    s = store.snapshot()["sessions"][0]
    assert len(s["outcome"]["grabs"]) == 1
    assert s["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH1"
    assert store.snapshot()["unmatched_grabs"] == []


def test_record_grab_correlates_by_original_title():
    store = _store_with_matched_session()
    store.record_grab("TFG.26.07.07.Latex.Worship.Session.1080p", "HASH2")
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH2"


def test_record_grab_picks_newest_matching_session():
    store = SessionStore(max_sessions=10, max_candidates=200)
    for _ in range(2):
        rec = store.recorder("empornium", 75, "q")
        rec.scored([(_cand("g1"), SCENE, _ms(90), "SAME rewritten")])
        rec.commit()
    store.record_grab("SAME rewritten", "HASH3")
    snap = store.snapshot()["sessions"]
    assert len(snap[0]["outcome"]["grabs"]) == 1   # newest
    assert snap[1]["outcome"]["grabs"] == []        # older untouched


def test_record_grab_unmatched_is_kept_and_bounded():
    store = SessionStore(max_sessions=10, max_candidates=200)
    for i in range(25):
        store.record_grab(f"Never.Seen.{i}", f"H{i}")
    grabs = store.snapshot()["unmatched_grabs"]
    assert len(grabs) == 20                            # bounded
    assert grabs[0]["grab"]["release_title"] == "Never.Seen.24"  # newest first


def test_record_import_stamps_grabbed_session():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    imp = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]["imported"]
    assert imp["movie_id"] == 7 and imp["dry_run"] is False


def test_record_import_dry_run_flagged():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=True)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["imported"]["dry_run"] is True


def test_record_import_stamps_unmatched_grab():
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.record_grab("Never.Seen.Release", "HASHX")
    store.record_import("HASHX", movie_id=9, file_count=2, dry_run=False)
    u = store.snapshot()["unmatched_grabs"][0]
    assert u["imported"]["movie_id"] == 9


def test_record_import_without_any_grab_surfaces():
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.record_import("GHOST", movie_id=3, file_count=1, dry_run=False)
    u = store.snapshot()["unmatched_grabs"][0]
    assert u["grab"]["download_id"] == "GHOST"
    assert u["imported"]["movie_id"] == 3


def _store_with_twin_titles(size_a=1000, size_b=2000):
    # Two candidates whose rewritten titles are IDENTICAL — the real-world
    # ambiguity this feature must survive (2026-07-14 trace).
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "That Fetish Girl 07.07.2026")
    rec.scored([
        (_cand("gA", "Release.A", size=size_a), SCENE, _ms(90), "SAME rewritten"),
        (_cand("gB", "Release.B", size=size_b), SCENE, _ms(90), "SAME rewritten"),
    ])
    rec.commit()
    return store


def test_record_grab_stamps_grabbed_guid_on_unique_title_match():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "g1"
    assert g["grab"]["size"] == 1000


def test_record_grab_without_size_still_stamps_unique_match():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "g1"


def test_record_grab_size_breaks_title_tie():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 2000)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "gB"
    assert g["grab"]["download_id"] == "HASH1"


def test_record_grab_tie_without_size_leaves_guid_none():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1")
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grab"] is not None                  # record still created
    assert g["grabbed_guid"] is None              # UI degrades to session level


def test_record_grab_unhelpful_size_leaves_guid_none():
    # size matches neither twin
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 3000)
    assert store.snapshot()["sessions"][0]["outcome"]["grabs"][0]["grabbed_guid"] is None
    # size matches both twins
    store2 = _store_with_twin_titles(size_a=1000, size_b=1000)
    store2.record_grab("SAME rewritten", "HASH2", 1000)
    g2 = store2.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g2["grab"] is not None
    assert g2["grabbed_guid"] is None


# --- multi-grab: a second grab APPENDS a second record (the whole feature) ---


def _store_with_two_rewrites():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("g2", "Release.Two", size=2000), SCENE, _ms(85), "Rewritten Two"),
    ])
    rec.commit()
    return store


def test_second_grab_appends_second_record():
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("Rewritten Two", "HASH2", 2000)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2
    assert grabs[0]["grab"]["download_id"] == "HASH1"
    assert grabs[0]["grabbed_guid"] == "g1"
    assert grabs[1]["grab"]["download_id"] == "HASH2"
    assert grabs[1]["grabbed_guid"] == "g2"
    assert store.snapshot()["unmatched_grabs"] == []


def test_ambiguous_second_grab_appends_record_with_none_guid():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("gA", "Release.A", size=500), SCENE, _ms(85), "SAME rewritten"),
        (_cand("gB", "Release.B", size=500), SCENE, _ms(85), "SAME rewritten"),
    ])
    rec.commit()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("SAME rewritten", "HASH2")  # ambiguous: no size, twin titles
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2
    assert grabs[0]["grabbed_guid"] == "g1"       # first record untouched
    assert grabs[1]["grab"]["download_id"] == "HASH2"
    assert grabs[1]["grabbed_guid"] is None       # its own ambiguity, its own None


def test_two_grabs_import_independently():
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("Rewritten Two", "HASH2", 2000)
    store.record_import("HASH2", movie_id=8, file_count=2, dry_run=False)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert grabs[0]["imported"] is None            # first grab: not yet imported
    assert grabs[1]["imported"]["movie_id"] == 8   # second grab: imported
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=True)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert grabs[0]["imported"]["dry_run"] is True
    assert grabs[1]["imported"]["dry_run"] is False
    assert store.snapshot()["unmatched_grabs"] == []


def test_regrab_same_download_id_updates_in_place():
    # Webhook resend / re-grab: same download_id must NOT duplicate the record,
    # and must keep the import stamp it already earned.
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 1
    assert grabs[0]["grabbed_guid"] == "g1"
    assert grabs[0]["imported"]["movie_id"] == 7   # import stamp survives


def test_empty_download_id_grabs_always_append():
    # A grab without a download_id can't be deduped; two of them = two records.
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "")
    store.record_grab("Rewritten Two", "")
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2


def test_import_with_empty_download_id_never_matches_a_grab():
    # An id-less import must not bind to an id-less grab record; it surfaces
    # as unmatched instead of stamping the wrong record.
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "")
    store.record_import("", movie_id=5, file_count=1, dry_run=False)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["imported"] is None
    assert store.snapshot()["unmatched_grabs"][0]["imported"]["movie_id"] == 5


def test_record_import_leaves_grabbed_guid_unchanged():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["imported"]["movie_id"] == 7
    assert g["grabbed_guid"] == "g1"
```

Note the two old overwrite-limitation tests (`test_record_grab_second_grab_restamps_guid`, `test_record_grab_ambiguous_second_grab_resets_guid`) are gone — replaced BY DESIGN with `test_second_grab_appends_second_record` and `test_ambiguous_second_grab_appends_record_with_none_guid`.

**Edit C** — in `tests/test_ui_api.py`, in `test_sessions_returns_snapshot_and_index`, replace line 60:

```python
    assert body["sessions"][0]["outcome"]["grabbed_guid"] is None
```

with:

```python
    assert body["sessions"][0]["outcome"]["grabs"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_observe.py tests/test_ui_api.py -q`
Expected: FAIL — `KeyError: 'grabs'` (and the old-shape correlation asserts) across the rewritten tests.

- [ ] **Step 3: Implement the model + correlation in `scenehound/observe.py`**

**3a.** Change the dataclass import (line 19):

```python
from dataclasses import dataclass, field
```

**3b.** Replace the `Outcome` dataclass (currently lines ~99–112) with:

```python
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
```

**3c.** Replace `record_grab` (currently lines ~167–191) with:

```python
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
        ev = GrabEvent(release_title, download_id, time.time(), size)
        for s in self._sessions:  # deque is newest-first already
            matches = [c for c in s.candidates
                       if release_title and (c.rewritten_title == release_title
                                             or c.title == release_title)]
            if not matches:
                continue
            guid = self._correlate_guid(matches, size)
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
        self._unmatched_grabs.appendleft(UnmatchedGrab(ev))
```

**3d.** Replace `record_import` (currently lines ~193–209) with:

```python
    @_shielded
    def record_import(self, download_id: str, movie_id: int,
                      file_count: int, dry_run: bool) -> None:
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
```

`snapshot()`, `UnmatchedGrab`, `GrabEvent`, `ImportEvent`, `Recorder.commit()` are all untouched — `dataclasses.asdict` recurses the new list automatically, and `commit()`'s `Outcome(...)` call sets no grab fields.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (305 baseline; the grab-block goes 16 → 20 tests, so expect **309 passed**; the exact count matters less than zero failures — `test_import_api.py` and `test_import_completer.py` must pass UNCHANGED).

- [ ] **Step 5: Commit**

```bash
git add scenehound/observe.py tests/test_observe.py tests/test_ui_api.py
git commit -m "feat: per-grab records on Outcome — second same-session grab appends, never overwrites

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: UI renders multi-grab sessions

**Files:**
- Modify: `scenehound/static/ui.html` (grabBadge ~153, badge ~160, candidates ~209–212, outcome ~237–248, render ~266–271)
- Test: `tests/test_ui_api.py` (marker tuple in `test_ui_page_has_app_markers`, ~line 73)

**Interfaces:**
- Consumes: snapshot JSON from Task 1 — `s.outcome.grabs` = array of `{grab: {release_title, download_id, at, size}, grabbed_guid, imported: {at, movie_id, file_count, dry_run} | null}`.
- Produces: `grabBadge(rec)` (takes one grab record, returns `[cls, label]`), `grabPills(o)` (returns array of counted summary pills), `badges(s)` (replaces `badge(s)`, returns array of `[cls, label]`).

- [ ] **Step 1: Extend the marker test (failing first)**

In `tests/test_ui_api.py`, `test_ui_page_has_app_markers`, replace the marker tuple:

```python
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid"):
```

with:

```python
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid",
                   "grabPills", "o.grabs"):
```

Run: `.venv/bin/python -m pytest tests/test_ui_api.py -q`
Expected: FAIL — `AssertionError: grabPills`.

- [ ] **Step 2: Rewrite the four rendering sites in `ui.html`**

**2a.** Replace `grabBadge` (lines ~153–158) with:

```js
function grabBadge(rec) {
  // rec is ONE grab record from o.grabs: {grab, grabbed_guid, imported}
  if (rec.imported) return rec.imported.dry_run
    ? ['b-dryrun', 'Would import (dry-run)'] : ['b-imported', 'Imported'];
  return ['b-grabbed', 'Grabbed'];
}

function grabPills(o) {
  // Session-summary pills: one counted pill per state present, strongest
  // first. ×N suffix omitted at N==1 so single-grab sessions look as before.
  const counts = { 'b-imported': 0, 'b-dryrun': 0, 'b-grabbed': 0 };
  const labels = { 'b-imported': 'Imported', 'b-dryrun': 'Would import (dry-run)',
                   'b-grabbed': 'Grabbed' };
  for (const rec of (o.grabs || [])) counts[grabBadge(rec)[0]]++;
  return Object.keys(counts).filter(k => counts[k]).map(k =>
    [k, labels[k] + (counts[k] > 1 ? ` ×${counts[k]}` : '')]);
}
```

**2b.** Replace `badge` (lines ~160–169) with a plural version:

```js
function badges(s) {
  const o = s.outcome;
  const g = grabPills(o);
  if (g.length) return g;
  if (s.kind === 'rss') return [['b-rss', `RSS: ${o.rewritten} rewritten of ${o.items_total}`]];
  if (s.kind === 'passthrough') return [['b-pass', `Passthrough (${o.matched_count})`]];
  if (o.status === 'error') return [['b-error', 'Error']];
  if (o.status === 'matched') return [['b-matched', `Matched (${o.matched_count})`]];
  return [['b-empty', 'No matches']];
}
```

**2c.** In `candidates(s)`, replace the per-row badge lookup (lines ~209–212):

```js
  for (const c of s.candidates) {
    // grabbed_guid may be absent on sessions recorded before this feature.
    const gb = s.outcome.grabbed_guid && c.guid === s.outcome.grabbed_guid
      ? grabBadge(s.outcome) : null;
```

with:

```js
  const recByGuid = {};
  for (const rec of (s.outcome.grabs || []))
    if (rec.grabbed_guid) recByGuid[rec.grabbed_guid] = rec;
  for (const c of s.candidates) {
    // Each grabbed row badges with ITS OWN grab's state.
    const gb = recByGuid[c.guid] ? grabBadge(recByGuid[c.guid]) : null;
```

(The row-emitting template string below is unchanged — it already renders `gb`.)

**2d.** In `outcome(s)`, replace the single grab/import ladder lines (~237–248):

```js
  if (o.grab) {
    const gc = o.grabbed_guid
      ? (s.candidates || []).find(c => c.guid === o.grabbed_guid) : null;
    h += `<li>✓ Whisparr grabbed <code>${esc(o.grab.release_title)}</code>` +
      (gc && gc.title !== o.grab.release_title
        ? ` — candidate <code>${esc(gc.title)}</code>` : "") +
      ` at ${fmtTime(o.grab.at)}.</li>`;
  }
  if (o.imported)
    h += o.imported.dry_run
      ? `<li>✓ Scenehound <b>would have imported</b> ${o.imported.file_count} file(s) (dry-run) at ${fmtTime(o.imported.at)}.</li>`
      : `<li>✓ Scenehound auto-imported ${o.imported.file_count} file(s) at ${fmtTime(o.imported.at)}.</li>`;
```

with:

```js
  for (const rec of (o.grabs || [])) {
    const gc = rec.grabbed_guid
      ? (s.candidates || []).find(c => c.guid === rec.grabbed_guid) : null;
    h += `<li>✓ Whisparr grabbed <code>${esc(rec.grab.release_title)}</code>` +
      (gc && gc.title !== rec.grab.release_title
        ? ` — candidate <code>${esc(gc.title)}</code>` : "") +
      ` at ${fmtTime(rec.grab.at)}.</li>`;
    if (rec.imported)
      h += rec.imported.dry_run
        ? `<li>✓ Scenehound <b>would have imported</b> ${rec.imported.file_count} file(s) (dry-run) at ${fmtTime(rec.imported.at)}.</li>`
        : `<li>✓ Scenehound auto-imported ${rec.imported.file_count} file(s) at ${fmtTime(rec.imported.at)}.</li>`;
  }
```

**2e.** In `render(...)`, replace the session-card pill emission (~266–271):

```js
    const [cls, label] = badge(s);
    return `<details class="card" data-sid="${s.session_id}" ${open.has(String(s.session_id)) ? "open" : ""}>` +
      `<summary><span class="when">${fmtTime(s.started_at)}</span>` +
      `<span class="slug">${esc(s.slug)}</span>` +
      `<span class="ask">${esc(humanAsk(s))}</span>` +
      `<span class="badge ${cls}">${esc(label)}</span></summary>` +
```

with:

```js
    const pills = badges(s).map(([cls, label]) =>
      `<span class="badge ${cls}">${esc(label)}</span>`).join(" ");
    return `<details class="card" data-sid="${s.session_id}" ${open.has(String(s.session_id)) ? "open" : ""}>` +
      `<summary><span class="when">${fmtTime(s.started_at)}</span>` +
      `<span class="slug">${esc(s.slug)}</span>` +
      `<span class="ask">${esc(humanAsk(s))}</span>` +
      `${pills}</summary>` +
```

The `unmatched_grabs` rendering block in `render()` is untouched (its `u.grab`/`u.imported` shape didn't change) — but note it previously reused `grabBadge`? It does NOT (it builds its own badge markup inline), so no adjustment needed.

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (marker test now green; `render()` no longer references the removed `badge()`).

Sanity-grep that no stale single-slot reads remain:
Run: `grep -n "o\.grab\b\|o\.imported\b\|outcome\.grabbed_guid" scenehound/static/ui.html`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add scenehound/static/ui.html tests/test_ui_api.py
git commit -m "feat: multi-grab UI — counted state pills, per-row badges, multi-line outcome ladder

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Headless render verification (end-to-end DOM check)

The single-file UI has no JS test framework; the proven pre-PR verification (v0.4.0) is rendering the real page against a real `SessionStore` stub with the playwright-cache `chrome-headless-shell` and asserting on the dumped DOM. No repo changes expected from this task — it is evidence-gathering; fix and amend the prior commits only if it exposes a rendering bug.

**Files:**
- Create (scratch, NOT committed): `<scratchpad>/serve_stub.py` — wherever `<scratchpad>` appears in this task, substitute the scratchpad directory listed in your own system prompt (never a path inside the repo)
- Read-only: `scenehound/static/ui.html`, `scenehound/ui_api.py`

**Interfaces:**
- Consumes: Task 1's store behavior + Task 2's rendering, end to end.
- Produces: DOM evidence that (a) a 3-grab session shows `Imported ×2` + `Grabbed` pills, (b) each grabbed row badges independently, (c) the ladder lists 3 grab lines and 2 import lines, (d) a single-grab session renders with a plain `Imported` pill (no `×1`).

- [ ] **Step 1: Write the stub server**

Write `<scratchpad>/serve_stub.py`:

```python
"""Serve /ui against a stubbed SessionStore holding a 3-grab RSS session
(2 imported + 1 grabbed) and a 1-grab search session (imported)."""
from datetime import date

import uvicorn
from fastapi import FastAPI

from scenehound.api import AppState, IndexHolder
from scenehound.config import Config, IndexerConfig, ServiceConfig
from scenehound.matcher import MatchScore
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.observe import SessionStore
from scenehound.rate_limiter import TokenBucket
from scenehound.ui_api import ui_router
from scenehound.wanted_index import WantedIndex

cfg = Config(
    whisparr=ServiceConfig("http://w:6969", "wk"),
    prowlarr=ServiceConfig("http://p:9696", "pk"),
    indexers=(IndexerConfig("empornium", 12),),
    api_key="shk",
)
scene = SceneFingerprint(
    scene_id=7, site="That Fetish Girl", site_aliases=("TFG",),
    date=date(2026, 7, 7), title="Latex Worship Session",
    performers=("Jane Doe",),
)


def _cand(guid, title, size):
    return ReleaseCandidate(title=title, guid=guid, link="http://p/dl",
                            size=size, seeders=5)


def _ms():
    return MatchScore(90, ("date", "site"), None, {"date": 40.0, "site": 35.0})


store = SessionStore(max_sessions=10, max_candidates=200)

# Session 1 (older): single grab, imported — must render EXACTLY as before
# (plain "Imported" pill, no ×1).
rec = store.recorder("empornium", 75, "That Fetish Girl 07.07.2026")
rec.scored([(_cand("gS", "Single.Release", 500), scene, _ms(), "Rewritten Single")])
rec.commit()
store.record_grab("Rewritten Single", "HASH-S", 500)
store.record_import("HASH-S", movie_id=7, file_count=1, dry_run=False)

# Session 2 (newest): RSS session, three candidates all grabbed, two imported.
rec = store.recorder("empornium", 75, "")
rec.rss_summary(5, [
    (_cand("gA", "Rel.A", 1000), scene, _ms(), "Rewritten A"),
    (_cand("gB", "Rel.B", 2000), scene, _ms(), "Rewritten B"),
    (_cand("gC", "Rel.C", 3000), scene, _ms(), "Rewritten C"),
])
rec.commit()
store.record_grab("Rewritten A", "HASH-A", 1000)
store.record_grab("Rewritten B", "HASH-B", 2000)
store.record_grab("Rewritten C", "HASH-C", 3000)
store.record_import("HASH-A", movie_id=7, file_count=1, dry_run=False)
store.record_import("HASH-B", movie_id=7, file_count=1, dry_run=False)

holder = IndexHolder()
holder.set(WantedIndex([]))
app = FastAPI()
app.include_router(ui_router)
app.state.scenehound = AppState(
    config=cfg, prowlarr=None, index_holder=holder,
    buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    store=store,
)
uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
```

- [ ] **Step 2: Serve, dump the DOM, assert**

```bash
cd /Users/jamesking/VS/Scenehound
.venv/bin/python <scratchpad>/serve_stub.py & SRV=$!
sleep 2
SHELL_BIN=$(ls -d "$HOME/Library/Caches/ms-playwright"/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell | sort -V | tail -1)
"$SHELL_BIN" --headless --disable-gpu --no-sandbox --virtual-time-budget=8000 \
  --dump-dom "http://127.0.0.1:8765/ui?apikey=shk" > <scratchpad>/dom.html
kill $SRV
grep -c 'Imported ×2' <scratchpad>/dom.html          # expect 1  (RSS session pill)
grep -c 'Whisparr grabbed' <scratchpad>/dom.html      # expect 4  (3 RSS + 1 single)
grep -c 'auto-imported' <scratchpad>/dom.html         # expect 3  (2 RSS + 1 single)
grep -c '×1' <scratchpad>/dom.html                    # expect 0  (no ×1 suffix ever)
grep -c 'not correlated to a recorded search' <scratchpad>/dom.html  # expect 0
```

Also verify per-row badges land on the right rows: in `dom.html`, the table row containing `Rel.A` must contain `>Imported<`, the row containing `Rel.C` must contain `>Grabbed<` (e.g. `grep -o 'Rel\.A[^<]*<[^>]*>[^<]*' <scratchpad>/dom.html` or open the file and inspect the two rows). The `grep -c` expects that return 0 make the command exit non-zero — run them individually, don't `&&`-chain them.

- [ ] **Step 3: Record the evidence**

Paste the grep counts (and the two row snippets) into the task report. If any expectation fails, this is a rendering bug: fix `ui.html` (or `observe.py`), re-run the FULL suite, re-run this DOM check, and amend/extend the responsible commit before proceeding.

---

### Task 4: PR

**Files:** none (git/gh only).

- [ ] **Step 1: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 failures.

- [ ] **Step 2: Push branch and open PR (do NOT merge)**

```bash
git push -u origin feat/multi-grab-rss-badge
gh pr create --title "feat: same-session multi-grab badges (per-grab records on Outcome)" --body "$(cat <<'EOF'
## Summary
- Fixes the documented v0.2.0 accepted limitation, bitten live: when two candidates in ONE RSS session are both grabbed, the second Grab webhook overwrote the first.
- `Outcome.grabs: list[GrabRecord]` replaces the single `grab`/`grabbed_guid`/`imported` slots; each record bundles its grab event, correlated candidate guid, and independent import stamp (correlated by its own `download_id`).
- Guid tie-break logic (title match → size disambiguation → None) preserved exactly, now per record; idempotent on webhook resends (same non-empty `download_id` updates in place).
- UI: counted state pills on the session card (`Imported ×2` + `Grabbed`), per-row badge showing each grabbed row's own state, one outcome-ladder entry per grab. Single-grab sessions render pixel-identical to before.
- `unmatched_grabs` keeps its role: events correlating to no session at all.

Spec: `docs/superpowers/specs/2026-07-16-multi-grab-rss-badge-design.md`
Plan: `docs/superpowers/plans/2026-07-16-multi-grab-rss-badge.md`

## Test plan
- [x] Full suite green (`.venv/bin/python -m pytest`)
- [x] Headless DOM verification: 3-grab RSS session renders counted pills, independent row badges, multi-line ladder; single-grab session unchanged

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Note for the reviewer in this repo's flow: the user integrates; the PR stays open. After merge, the user does the grouped release (ONE version bump from 0.4.0 + tag covering PR #20 + PR #21 + this PR).
