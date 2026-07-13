# Grabbed-Candidate Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark, in the web UI's candidates table, exactly which candidate Whisparr actually grabbed (and later imported), using size to break ties between candidates with identical rewritten titles.

**Architecture:** `SessionStore.record_grab` already finds the matching candidate by title when correlating the Grab webhook to a session, but discards the candidate identity. This plan keeps that pointer: `Outcome` (the deliberately-mutable part of an otherwise frozen session) gains `grabbed_guid`, stamped in the same place `outcome.grab` is stamped. The webhook handler forwards the payload's `release.size` (currently ignored) so identical-title ties can be broken by exact size match. The UI badges the matching candidate row in place and enriches the outcome ladder with the original release title. Unresolvable ambiguity degrades to exactly today's behavior (`grabbed_guid = None`, no row badged).

**Tech Stack:** Python 3.12/3.14, FastAPI, pytest. Single self-contained HTML page with inline vanilla JS (no build step).

**Spec:** `docs/superpowers/specs/2026-07-14-grabbed-candidate-ui-design.md` (authoritative; this plan implements it).

## Global Constraints

- Work on branch `feat/grabbed-candidate-marker`. Never commit to `main`.
- Strict TDD: every task writes the failing test first, watches it fail, then implements.
- Run tests with: `.venv/bin/python3.12 -m pytest -q` (CI also runs 3.14 — use only 3.12-compatible syntax).
- `observe.py` isolation rules hold: it imports nothing from `api.py`, `import_api.py`, or FastAPI; no public method of `SessionStore`/`Recorder` may raise (`@_shielded` everywhere).
- `CandidateTrace.guid` is identity-only: it is compared, never rendered as visible text in the UI.
- All new dataclass fields get defaults so existing construction sites keep working.
- Follow the terse, rationale-carrying comment style already in `observe.py`.

## File Structure

- `scenehound/observe.py` — **modify.** `GrabEvent.size`, `Outcome.grabbed_guid`, tie-breaking correlation in `record_grab`.
- `scenehound/import_api.py` — **modify.** Extract `release.size` defensively; pass to `record_grab`.
- `scenehound/static/ui.html` — **modify.** Factor a shared `grabBadge(o)` helper; badge the grabbed candidate row; original title in the outcome ladder.
- `tests/test_observe.py` — **modify.** Correlation tests (unique, size tie-break, unresolvable tie, restamp, import untouched) + snapshot shape.
- `tests/test_import_api.py` — **modify.** `FakeStore` gains `size`; size parsing tests.
- `tests/test_ui_api.py` — **modify.** Snapshot shape + page marker.

---

### Task 1: `observe.py` — record which candidate the grab matched

**Files:**
- Modify: `scenehound/observe.py:83-107` (`GrabEvent`, `Outcome`), `scenehound/observe.py:162-175` (`record_grab`)
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: existing `CandidateTrace.size` / `.guid` / `.title` / `.rewritten_title` (all already stored per candidate).
- Produces: `SessionStore.record_grab(release_title: str, download_id: str, size: int | None = None) -> None`; `GrabEvent` gains field `size: int | None = None`; `Outcome` gains field `grabbed_guid: str | None = None`. Task 2 calls the new `record_grab` signature; Task 3 reads `outcome.grabbed_guid` and `outcome.grab.size` from the snapshot JSON.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/grabbed-candidate-marker
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_observe.py`, first extend the existing `_cand` helper (near line 84) with a `size` parameter — the tie-break tests need per-candidate sizes:

```python
def _cand(guid="g1", title="TFG.26.07.07.Latex.Worship.Session.1080p", size=1000):
    return ReleaseCandidate(title=title, guid=guid, link="http://p/dl?apikey=SECRET",
                            size=size, seeders=5)
```

Add one line to the existing `test_snapshot_is_json_serializable_and_complete` (after the `assert s["outcome"]["grab"] is None` line):

```python
    assert s["outcome"]["grabbed_guid"] is None
```

Then append these tests at the end of the file (after `test_record_import_without_any_grab_surfaces`):

```python
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
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabbed_guid"] == "g1"
    assert s["outcome"]["grab"]["size"] == 1000


def test_record_grab_without_size_still_stamps_unique_match():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    assert store.snapshot()["sessions"][0]["outcome"]["grabbed_guid"] == "g1"


def test_record_grab_size_breaks_title_tie():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 2000)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabbed_guid"] == "gB"
    assert s["outcome"]["grab"]["download_id"] == "HASH1"


def test_record_grab_tie_without_size_leaves_guid_none():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1")
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grab"] is not None       # session-level grab still stamps
    assert s["outcome"]["grabbed_guid"] is None   # UI degrades to old behavior


def test_record_grab_unhelpful_size_leaves_guid_none():
    # size matches neither twin
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 3000)
    assert store.snapshot()["sessions"][0]["outcome"]["grabbed_guid"] is None
    # size matches both twins
    store2 = _store_with_twin_titles(size_a=1000, size_b=1000)
    store2.record_grab("SAME rewritten", "HASH2", 1000)
    s2 = store2.snapshot()["sessions"][0]
    assert s2["outcome"]["grab"] is not None
    assert s2["outcome"]["grabbed_guid"] is None


def test_record_grab_second_grab_restamps_guid():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("g2", "Release.Two", size=2000), SCENE, _ms(85), "Rewritten Two"),
    ])
    rec.commit()
    store.record_grab("Rewritten One", "HASH1", 1000)
    assert store.snapshot()["sessions"][0]["outcome"]["grabbed_guid"] == "g1"
    store.record_grab("Rewritten Two", "HASH2", 2000)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grab"]["download_id"] == "HASH2"
    assert s["outcome"]["grabbed_guid"] == "g2"


def test_record_grab_ambiguous_second_grab_resets_guid():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("gA", "Release.A", size=500), SCENE, _ms(85), "SAME rewritten"),
        (_cand("gB", "Release.B", size=500), SCENE, _ms(85), "SAME rewritten"),
    ])
    rec.commit()
    store.record_grab("Rewritten One", "HASH1", 1000)
    assert store.snapshot()["sessions"][0]["outcome"]["grabbed_guid"] == "g1"
    store.record_grab("SAME rewritten", "HASH2")  # ambiguous: no size, twin titles
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grab"]["download_id"] == "HASH2"
    assert s["outcome"]["grabbed_guid"] is None   # restamped WITH the grab, never stale


def test_record_import_leaves_grabbed_guid_unchanged():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["imported"]["movie_id"] == 7
    assert s["outcome"]["grabbed_guid"] == "g1"
```

- [ ] **Step 3: Run the new tests to verify they fail**

```bash
.venv/bin/python3.12 -m pytest tests/test_observe.py -q
```

Expected: the new tests FAIL (`KeyError: 'grabbed_guid'` / unexpected keyword `size`); every pre-existing test still PASSES (the `_cand` extension is backward-compatible).

- [ ] **Step 4: Implement in `scenehound/observe.py`**

`GrabEvent` (line ~83) gains a defaulted `size` — the existing `GrabEvent("", download_id, ev.at)` construction in `record_import` keeps working unchanged:

```python
@dataclass(frozen=True)
class GrabEvent:
    release_title: str
    download_id: str
    at: float
    size: int | None = None     # webhook release.size; tie-breaker only
```

`Outcome` (line ~98) gains `grabbed_guid` at the end of the field list:

```python
    grab: GrabEvent | None = None
    imported: ImportEvent | None = None
    # guid of the candidate the grab correlated to; None when the grab was
    # ambiguous (identical titles, size missing or unhelpful) — the UI then
    # degrades to session-level display only.
    grabbed_guid: str | None = None
```

Replace `record_grab` (lines 162-175) entirely:

```python
    @_shielded
    def record_grab(self, release_title: str, download_id: str,
                    size: int | None = None) -> None:
        ev = GrabEvent(release_title, download_id, time.time(), size)
        # Accepted limitation (v0.2.0): if a second grab correlates to the
        # same session, it overwrites outcome.grab here; the earlier grab's
        # import (if any) then surfaces via unmatched_grabs instead of being
        # lost silently. grabbed_guid is restamped together with grab
        # (possibly back to None) so the pair can never drift apart.
        for s in self._sessions:  # deque is newest-first already
            matches = [c for c in s.candidates
                       if release_title and (c.rewritten_title == release_title
                                             or c.title == release_title)]
            if not matches:
                continue
            if len(matches) > 1 and size is not None:
                # Twin releases of one scene can rewrite to identical titles;
                # the webhook's size is what tells them apart.
                by_size = [c for c in matches if c.size == size]
                if len(by_size) == 1:
                    matches = by_size
            s.outcome.grab = ev
            s.outcome.grabbed_guid = matches[0].guid if len(matches) == 1 else None
            return
        self._unmatched_grabs.appendleft(UnmatchedGrab(ev))
```

- [ ] **Step 5: Run the full observe suite**

```bash
.venv/bin/python3.12 -m pytest tests/test_observe.py -q
```

Expected: all PASS.

- [ ] **Step 6: Run the whole test suite (nothing else may regress)**

```bash
.venv/bin/python3.12 -m pytest -q
```

Expected: all PASS. (`import_api.py` still calls `record_grab` with two args — the `size` default keeps it valid until Task 2.)

- [ ] **Step 7: Commit**

```bash
git add scenehound/observe.py tests/test_observe.py
git commit -m "feat: record which candidate a grab correlated to, size-breaking title ties"
```

---

### Task 2: `import_api.py` — forward `release.size` from the Grab webhook

**Files:**
- Modify: `scenehound/import_api.py:27-36`
- Test: `tests/test_import_api.py`

**Interfaces:**
- Consumes: `SessionStore.record_grab(release_title, download_id, size)` from Task 1.
- Produces: no new interface; the webhook route now passes a third positional arg `size: int | None` to `record_grab`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_import_api.py`, update `FakeStore` (line ~76) to mirror the real signature and record 3-tuples:

```python
class FakeStore:
    def __init__(self):
        self.grabs = []

    def record_grab(self, release_title, download_id, size=None):
        self.grabs.append((release_title, download_id, size))
```

Update the two existing assertions that check `fs.grabs` to expect 3-tuples:

In `test_webhook_grab_records_and_still_notifies`:

```python
    assert fs.grabs == [("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", None)]
```

In `test_webhook_grab_download_id_fallback_inside_release`:

```python
    assert fs.grabs == [("T", "HASH2", None)]
```

Append the new tests at the end of the file:

```python
def test_webhook_grab_records_release_size():
    fc, fs = FakeCompleter(), FakeStore()
    payload = {"eventType": "Grab",
               "release": {"releaseTitle": "T", "size": 2469606195},
               "downloadId": "H"}
    TestClient(_app_with_store(fc, fs)).post("/import/webhook?apikey=shk", json=payload)
    assert fs.grabs == [("T", "H", 2469606195)]


def test_webhook_grab_junk_size_degrades_to_none():
    fc, fs = FakeCompleter(), FakeStore()
    payload = {"eventType": "Grab",
               "release": {"releaseTitle": "T", "size": "not a number"},
               "downloadId": "H"}
    r = TestClient(_app_with_store(fc, fs)).post(
        "/import/webhook?apikey=shk", json=payload)
    assert r.status_code == 200
    assert fs.grabs == [("T", "H", None)]


def test_webhook_grab_absent_release_dict_records_with_none_size():
    fc, fs = FakeCompleter(), FakeStore()
    payload = {"eventType": "Grab", "downloadId": "H"}   # no release object at all
    r = TestClient(_app_with_store(fc, fs)).post(
        "/import/webhook?apikey=shk", json=payload)
    assert r.status_code == 200
    assert fs.grabs == [("", "H", None)]
```

- [ ] **Step 2: Run tests to verify the new ones fail**

```bash
.venv/bin/python3.12 -m pytest tests/test_import_api.py -q
```

Expected: `test_webhook_grab_records_release_size` FAILS (size recorded as `None` because the route passes only two args). The junk-size test may pass trivially already — that's fine; it pins the behavior. Everything else PASSES.

- [ ] **Step 3: Implement in `scenehound/import_api.py`**

Inside the `if event == "Grab":` block, after the `download_id` extraction (line ~34), add size extraction and pass it through:

```python
            title = str(release.get("releaseTitle") or "")
            download_id = str(payload.get("downloadId")
                              or release.get("downloadId") or "")
            raw_size = release.get("size")
            try:
                size = int(raw_size) if raw_size is not None else None
            except (TypeError, ValueError):
                size = None
            if title or download_id:
                store.record_grab(title, download_id, size)
```

- [ ] **Step 4: Run the full suite**

```bash
.venv/bin/python3.12 -m pytest -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scenehound/import_api.py tests/test_import_api.py
git commit -m "feat: forward Grab webhook release.size for candidate tie-breaking"
```

---

### Task 3: `ui.html` — badge the grabbed row; original title in the outcome ladder

**Files:**
- Modify: `scenehound/static/ui.html:150-160` (`badge`), `:194-210` (`candidates`), `:212-232` (`outcome`)
- Test: `tests/test_ui_api.py`

**Interfaces:**
- Consumes: `outcome.grabbed_guid` (string or null, may be absent in pre-change sessions) and `candidates[].guid` from the snapshot JSON (Task 1).
- Produces: nothing downstream; final task.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ui_api.py`, add the snapshot-shape assertion to the existing `test_sessions_returns_snapshot_and_index`, after the `assert body["unmatched_grabs"] == []` line:

```python
    assert body["sessions"][0]["outcome"]["grabbed_guid"] is None
```

And add `"grabbed_guid"` to the marker list in `test_ui_page_has_app_markers`:

```python
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid"):
```

- [ ] **Step 2: Run tests to verify the marker test fails**

```bash
.venv/bin/python3.12 -m pytest tests/test_ui_api.py -q
```

Expected: `test_ui_page_has_app_markers` FAILS on `grabbed_guid` (the page doesn't reference it yet); the snapshot-shape assertion PASSES (Task 1 delivered the field).

- [ ] **Step 3: Implement in `scenehound/static/ui.html`**

**(a)** Factor the grab/import branch out of `badge(s)` (line ~150) so the row badge and the session badge can never disagree. Replace the `badge` function with:

```js
function grabBadge(o) {
  if (o.imported) return o.imported.dry_run
    ? ['b-dryrun', 'Would import (dry-run)'] : ['b-imported', 'Imported'];
  if (o.grab) return ['b-grabbed', 'Grabbed'];
  return null;
}

function badge(s) {
  const o = s.outcome;
  const g = grabBadge(o);
  if (g) return g;
  if (s.kind === 'rss') return ['b-rss', `RSS: ${o.rewritten} rewritten of ${o.items_total}`];
  if (s.kind === 'passthrough') return ['b-pass', `Passthrough (${o.matched_count})`];
  if (o.status === 'error') return ['b-error', 'Error'];
  if (o.status === 'matched') return ['b-matched', `Matched (${o.matched_count})`];
  return ['b-empty', 'No matches'];
}
```

**(b)** In `candidates(s)` (line ~200), badge the grabbed row in place. Replace the loop body's opening with:

```js
  for (const c of s.candidates) {
    // grabbed_guid may be absent on sessions recorded before this feature.
    const gb = s.outcome.grabbed_guid && c.guid === s.outcome.grabbed_guid
      ? grabBadge(s.outcome) : null;
    h += `<tr class="${c.matched ? "conf-hit" : ""}">` +
      `<td>${esc(c.title)}${gb ? ` <span class="badge ${gb[0]}">${esc(gb[1])}</span>` : ""}${c.rewritten_title
          ? `<div class="muted">→ returned as: ${esc(c.rewritten_title)}</div>` : ""}` +
      `<div class="why">${whyLines(c, s.threshold).join("<br>")}</div></td>` +
      `<td class="num">${fmtSize(c.size)}</td><td class="num">${c.seeders ?? ""}</td>` +
      `<td class="num">${c.confidence} <span class="confbar" style="width:${c.confidence * 0.6}px"></span></td>` +
      `<td>${c.matched ? "✓" : ""}</td></tr>`;
  }
```

**(c)** In `outcome(s)` (line ~225), replace the `if (o.grab)` line-builder so the ladder names the original release title when it resolves and differs from the grabbed (rewritten) one:

```js
  if (o.grab) {
    const gc = o.grabbed_guid
      ? (s.candidates || []).find(c => c.guid === o.grabbed_guid) : null;
    h += `<li>✓ Whisparr grabbed <code>${esc(o.grab.release_title)}</code>` +
      (gc && gc.title !== o.grab.release_title
        ? ` — candidate <code>${esc(gc.title)}</code>` : "") +
      ` at ${fmtTime(o.grab.at)}.</li>`;
  }
```

- [ ] **Step 4: Run the full suite**

```bash
.venv/bin/python3.12 -m pytest -q
```

Expected: all PASS.

- [ ] **Step 5: Eyeball the rendering (no JS test harness in this repo)**

Serve the app locally and load `/ui` with recorded sessions, or temporarily stub `poll()` with a fixture snapshot containing `grabbed_guid` set to a candidate guid. Verify: (1) exactly one candidate row shows the badge; (2) badge text matches the session badge (`Grabbed` before import, `Imported` after); (3) the outcome ladder shows "— candidate `<original title>`" when titles differ; (4) sessions without `grabbed_guid` render exactly as before. Revert any stub before committing.

- [ ] **Step 6: Commit**

```bash
git add scenehound/static/ui.html tests/test_ui_api.py
git commit -m "feat: UI badges the grabbed/imported candidate row in place"
```
