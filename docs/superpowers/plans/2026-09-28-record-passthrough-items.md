# Record Passed-Through Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the items Scenehound returns to Whisparr unchanged (RSS items below threshold, search-passthrough results), so a Whisparr grab of one correlates to a session instead of landing in `/ui`'s unmatched strip.

**Architecture:** `observe.py` gains a `passed_through` row kind (`CandidateTrace.passed_through`, `.scene`, nullable `scene_id`), a per-session count (`Outcome.passed_through`), and a pruning step in `SessionStore.add` that keeps each passed-through RSS item listed once, under the latest poll that returned it. `api.py` gets one helper, `_best_scene`, which both keeps today's RSS rewrite choice and finds the closest wanted scene for everything else; `_rss_mode` and `_passthrough` feed rows to a new `Recorder.passed_through`. `ui.html` renders them in a collapsed block per session. The same change redacts `authkey`/`torrent_pass` from stored guids and scrubs existing state files on load.

**Tech Stack:** Python 3.12/3.14, FastAPI, stdlib dataclasses, pytest; vanilla-JS single-file UI; chrome-headless-shell for DOM verification.

**Spec:** `docs/superpowers/specs/2026-09-28-record-passthrough-items-design.md`

## Global Constraints

- `observe.py` imports nothing from `api.py`, `import_completer.py`, `import_api.py`, or FastAPI.
- No public method of `SessionStore` or `Recorder` may raise: every mutator is `@_shielded`.
- Single writer on the event loop: nothing in `observe.py` awaits; no locking.
- Every new field decoded from the state file is read with a default, so a v0.6.1 `ui-sessions.json` loads unchanged.
- Response bytes never depend on recording: with the UI off (`store=None`) or on, every Torznab response is byte-identical.
- The RSS rewrite choice is unchanged: highest confidence at/above threshold, **first on ties**.
- No version bump in this branch. The release is a separate `chore: release 0.7.0` commit + tag after merge.
- Run tests with `.venv/bin/python -m pytest -q` from `/Users/jamesking/VS/Scenehound`. Baseline: 399 passed.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Comment style: terse, rationale-carrying, as already in `observe.py`/`api.py`.
- UI copy, verbatim:
  - block summary: `Passed through — not rewritten (N)`
  - RSS-only suffix: ` · M more listed under later polls`
  - all-moved line: `Passed through unchanged: all M are listed under later polls.`
  - no-scene row: `No wanted scene shares a date or name with this release.`
  - closest row: `Closest wanted scene: <b>Site — Title</b> (date)`
  - below threshold: `N < threshold T → not rewritten, passed through unchanged`
  - at/above threshold: `N ≥ threshold T → would have matched, but this query path returns results unchanged`
  - ladder RSS suffix: `, N passed through unchanged`
  - ladder grab suffix: ` — passed through unchanged (Scenehound's best: <confidence>[, <veto>])`
  - strip copy: `not correlated to any recorded search or RSS poll`
  - RSS no-index note: `wanted list not loaded — items passed through unscored`

## Review Focus

1. **The same release twice in one RSS response** (duplicate guid and title) — both rows are recorded; a grab still correlates to the session (at session level, no row badge, since the tie is ambiguous) and never lands in the unmatched strip. Test: Task 2.
2. **A restart between polls** — sessions restored from `ui-sessions.json` are pruned by the next live poll exactly like in-memory ones. Test: Task 3.
3. **Pre-feature RSS sessions after the upgrade** — they have no rows and `Outcome.passed_through` decodes to 0, so the UI must not claim their items are "listed under later polls". Tests: Task 2 (decoder), Task 6 (DOM).
4. **A grabbed row outliving several later polls** — it survives every subsequent prune, and a second grab of the same item lands on the newest poll. Test: Task 3.
5. **A candidate with an empty guid** — never used as a pruning key, so it can't wipe unrelated guid-less rows from older polls. Test: Task 3.

---

### Task 1: Redact tracker secrets from stored guids and scrub old state files

**Files:**
- Modify: `scenehound/observe.py` (`_SECRET_PARAM` line 32; `_candidate` lines 188–197; `_outcome` lines 214–222; `load` lines 293–322)
- Modify: `README.md` (Web UI section, the sentence at lines 145–147)
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_SECRET_PARAM` redacts `authkey` and `torrent_pass`; `_sanitize_opt(text: str | None) -> str | None`; `SessionStore.load()` leaves `_dirty = True` iff the file's raw text contained an unredacted secret.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
# ---- tracker secrets in guids ---------------------------------------------
# Both live trackers' guids are download URLs carrying authkey= and
# torrent_pass=. They must never be stored or served.

TRACKER_GUID = ("https://www.happyfappy.net/torrents.php?action=download"
                "&id=149855&authkey=AUTHKEY123&torrent_pass=PASS456")
REDACTED_GUID = ("https://www.happyfappy.net/torrents.php?action=download"
                 "&id=149855&authkey=REDACTED&torrent_pass=REDACTED")


def test_recorder_redacts_tracker_authkey_and_torrent_pass():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("happyfappy", 75, "q")
    rec.scored([(_cand(TRACKER_GUID), SCENE, _ms(90), "rewritten")])
    rec.commit()
    snap = store.snapshot()
    assert snap["sessions"][0]["candidates"][0]["guid"] == REDACTED_GUID
    assert "AUTHKEY123" not in json.dumps(snap)
    assert "PASS456" not in json.dumps(snap)


def _write_unredacted_state(path):
    # A v0.6.1 file: add() stores what it's given, so building the session by
    # hand reproduces the unredacted guid and grabbed_guid an old build wrote.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate(guid=TRACKER_GUID)],
                           status="matched", matched_count=1))
    store.record_grab("That Fetish Girl 2026-07-07 X 1080p", "HASH1", 1000)
    store.save(path)
    assert "AUTHKEY123" in path.read_text()


def test_load_scrubs_tracker_secrets_and_rewrites_the_file(tmp_path):
    path = tmp_path / "ui-sessions.json"
    _write_unredacted_state(path)

    restored = _restore(path)
    s = restored.snapshot()["sessions"][0]
    assert s["candidates"][0]["guid"] == REDACTED_GUID
    # Both sides of the correlation key were scrubbed the same way, so the
    # restored grab still badges its row.
    assert s["outcome"]["grabs"][0]["grabbed_guid"] == REDACTED_GUID

    restored.save(path)  # the next flush tick
    text = path.read_text()
    assert "AUTHKEY123" not in text and "PASS456" not in text


def test_load_scrubbed_state_still_correlates_a_new_grab(tmp_path):
    path = tmp_path / "ui-sessions.json"
    _write_unredacted_state(path)

    restored = _restore(path)
    restored.record_grab("That Fetish Girl 2026-07-07 X 1080p", "HASH2", 1000)
    grabs = restored.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert [g["grabbed_guid"] for g in grabs] == [REDACTED_GUID, REDACTED_GUID]
    assert restored.snapshot()["unmatched_grabs"] == []


def test_load_of_a_clean_file_does_not_rewrite_it(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    path.unlink()
    restored.save(path)
    assert not path.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q -k "tracker or scrub or clean_file"`
Expected: FAIL. `test_recorder_redacts_tracker_authkey_and_torrent_pass` and the two scrub tests fail on the unredacted guid. `test_load_of_a_clean_file_does_not_rewrite_it` passes already (it guards the dirty flag you're about to change).

- [ ] **Step 3: Implement the redaction**

In `scenehound/observe.py`, replace:

```python
_SECRET_PARAM = re.compile(r"(?i)\b(apikey|api_key|passkey|token)=[^&\s]+")
```

with:

```python
# authkey/torrent_pass: Gazelle trackers (empornium, happyfappy) put both in
# the download URL that Prowlarr hands us as the guid.
_SECRET_PARAM = re.compile(
    r"(?i)\b(apikey|api_key|passkey|token|authkey|torrent_pass)=[^&\s]+")
```

Below `_sanitize`, add:

```python
def _sanitize_opt(text: str | None) -> str | None:
    return _sanitize(text) if text else text
```

In `_candidate`, replace `title=d.get("title", ""), guid=d.get("guid", ""),` with:

```python
        # Re-sanitized on the way in: files written before authkey and
        # torrent_pass were redacted still carry them.
        title=d.get("title", ""), guid=_sanitize(d.get("guid") or ""),
```

In `_outcome`, replace `grabbed_guid=g.get("grabbed_guid"),` with:

```python
                          # Scrubbed exactly like the candidate guid it points
                          # at, so the correlation key still agrees.
                          grabbed_guid=_sanitize_opt(g.get("grabbed_guid")),
```

In `SessionStore.load`, replace:

```python
        data = json.loads(p.read_text(encoding="utf-8"))
```

with:

```python
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
```

and replace the `self._dirty = False` line near the end of `load` with:

```python
        # The decoders scrubbed any unredacted secret from memory; flag the
        # file so the next flush rewrites it too, instead of leaving the
        # secrets on disk until a search happens to dirty the store.
        self._dirty = _sanitize(text) != text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q`
Expected: PASS (all of `test_observe.py`).

- [ ] **Step 5: Correct the README's claim about the state file**

In `README.md`, replace:

```
The history is a bounded ring of the most recent ~50 searches, kept in memory and
mirrored to `/config/ui-sessions.json` so it survives a restart. That file holds
what the UI shows — scene titles, performer names, release titles, and match
reasoning; it never holds URLs or API keys. A hard `docker kill` loses at most
```

with:

```
The history is a bounded ring of the most recent ~50 searches, kept in memory and
mirrored to `/config/ui-sessions.json` so it survives a restart. That file holds
what the UI shows — scene titles, performer names, release titles, and match
reasoning — plus each release's tracker link, with API keys, passkeys and
tracker auth tokens redacted. A hard `docker kill` loses at most
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 403 passed.

- [ ] **Step 7: Commit**

```bash
git add scenehound/observe.py tests/test_observe.py README.md
git commit -m "$(cat <<'EOF'
fix: redact tracker authkey and torrent_pass from stored guids

Both live trackers' guids are download URLs carrying authkey= and
torrent_pass=, which _SECRET_PARAM let through into ui-sessions.json and
/ui/api/sessions. Existing state files are scrubbed on load and rewritten
on the next flush.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The passed-through row, its count, and `Recorder.passed_through`

**Files:**
- Modify: `scenehound/observe.py` (`CandidateTrace` lines 77–94; `Outcome` lines 125–136; `_candidate`; `_outcome`; `record_grab` log line ~375; `Recorder.__init__`, `rss_summary`, `commit`; `NullRecorder`)
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: Task 1's `_sanitize`.
- Produces:
  - `CandidateTrace.scene_id: int | None` (positional, no default; `None` = no wanted scene came close)
  - `CandidateTrace.passed_through: bool = False`
  - `CandidateTrace.scene: SceneRef | None = None`
  - `Outcome.passed_through: int = 0`
  - `Recorder.passed_through(items)` where `items` is an iterable of `(ReleaseCandidate, SceneFingerprint | None, MatchScore | None)`
  - `NullRecorder.passed_through(items)` no-op
  - `Recorder.rss_summary(items_total, matched)` sets `rewritten = len(matched)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
# ---- passed-through rows ----------------------------------------------------
# Items Scenehound returned to Whisparr unchanged. Recorded so a Whisparr grab
# of one correlates to its session instead of the unmatched strip.

def test_recorder_records_passed_through_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([
        (_cand("g1", "Raw.Near.Miss"), SCENE, _ms(70, strong=("site", "performer"))),
        (_cand(TRACKER_GUID, "Raw.Unrelated"), None, None),
    ])
    rec.rss_summary(2, [])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    near, unrelated = s["candidates"]          # confidence desc
    assert near["passed_through"] is True
    assert near["matched"] is False and near["rewritten_title"] is None
    assert near["scene_id"] == 7 and near["confidence"] == 70
    assert near["strong_signals"] == ["site", "performer"]
    assert near["scene"]["title"] == "Latex Worship Session"
    assert near["scene"]["date"] == "2026-07-07"
    assert unrelated["passed_through"] is True
    assert unrelated["scene_id"] is None and unrelated["scene"] is None
    assert unrelated["confidence"] == 0 and unrelated["strong_signals"] == []
    assert unrelated["veto"] is None and unrelated["detail"] == {}
    assert unrelated["guid"] == REDACTED_GUID
    assert s["outcome"]["passed_through"] == 2


def test_passed_through_row_above_threshold_is_never_matched():
    # A search passthrough result that WOULD clear the threshold is still
    # returned unchanged: matched means "Scenehound rewrote it".
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "familytherapyxxx 26.07.26")
    rec.fallback("unparseable-query")
    rec.passthrough_results(1)
    rec.passed_through([(_cand("g1"), SCENE, _ms(100))])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    c = s["candidates"][0]
    assert c["confidence"] == 100
    assert c["matched"] is False and c["passed_through"] is True
    assert s["outcome"]["matched_count"] == 1     # passthrough: results returned
    assert s["outcome"]["passed_through"] == 1


def test_rss_summary_counts_only_rewritten_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "Raw.1"), None, None),
                        (_cand("p2", "Raw.2"), None, None)])
    rec.rss_summary(3, [(_cand("g1"), SCENE, _ms(90), "rewritten")])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 1
    assert s["outcome"]["items_total"] == 3
    assert s["outcome"]["passed_through"] == 2
    assert len(s["candidates"]) == 3


def test_record_grab_correlates_a_passed_through_title():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "[Bellesa House] - Raw Tracker Title - 2023-06-22"),
                         None, None)])
    rec.rss_summary(1, [])
    rec.commit()

    store.record_grab("[Bellesa House] - Raw Tracker Title - 2023-06-22", "HASH1", 1000)
    snap = store.snapshot()
    assert snap["sessions"][0]["outcome"]["grabs"][0]["grabbed_guid"] == "p1"
    assert snap["unmatched_grabs"] == []


def test_duplicate_item_in_one_poll_still_correlates_to_the_session():
    # Review focus 1: the same release twice in one RSS response. The tie is
    # ambiguous (same title, same size), so no row badge -- but the grab must
    # still land on the session, never in the unmatched strip.
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "Raw.Dup"), None, None),
                        (_cand("p1", "Raw.Dup"), None, None)])
    rec.rss_summary(2, [])
    rec.commit()

    store.record_grab("Raw.Dup", "HASH1", 1000)
    snap = store.snapshot()
    assert len(snap["sessions"][0]["outcome"]["grabs"]) == 1
    assert snap["unmatched_grabs"] == []


def test_load_decodes_pre_feature_rows_with_defaults(tmp_path):
    # Review focus 3: a v0.6.1 file has none of the new keys.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    data = json.loads(path.read_text())
    cand = data["sessions"][0]["candidates"][0]
    for key in ("scene_id", "passed_through", "scene"):
        del cand[key]
    del data["sessions"][0]["outcome"]["passed_through"]
    path.write_text(json.dumps(data))

    s = _restore(path).snapshot()["sessions"][0]
    c = s["candidates"][0]
    assert c["scene_id"] is None
    assert c["passed_through"] is False
    assert c["scene"] is None
    assert s["outcome"]["passed_through"] == 0


def test_passed_through_rows_round_trip(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("g1", "Raw.Near"), SCENE, _ms(70, strong=("site",))),
                        (_cand("g2", "Raw.None"), None, None)])
    rec.rss_summary(2, [])
    rec.commit()
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    assert _restore(path).snapshot() == store.snapshot()
```

Also, in the existing `test_null_recorder_accepts_everything`, add this line after `NULL_RECORDER.passthrough_results(0)`:

```python
    NULL_RECORDER.passed_through([])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q`
Expected: FAIL. The new tests fail with `AttributeError: 'Recorder' object has no attribute 'passed_through'` (or, for `test_load_decodes_pre_feature_rows_with_defaults`, a `KeyError` on the missing keys), and `test_null_recorder_accepts_everything` fails with `AttributeError`.

- [ ] **Step 3: Implement the data model**

In `CandidateTrace`, replace `scene_id: int                    # best-matching scene` with:

```python
    scene_id: int | None             # best-matching scene; None = no wanted scene came close
```

and after the `residual` field add:

```python
    # Returned to Whisparr unmodified (RSS items below threshold, results of
    # a search passthrough). Recorded so a Whisparr grab of one correlates to
    # this session instead of landing in the unmatched strip.
    passed_through: bool = False
    # The closest wanted scene, on passed-through rows only: RSS and
    # passthrough sessions carry no scene list for the UI to name it from.
    scene: SceneRef | None = None
```

In `Outcome`, after `rewritten: int = 0               # RSS only` add:

```python
    passed_through: int = 0          # items returned unchanged, counted at capture
```

In `_candidate`, replace `scene_id=d.get("scene_id", 0), confidence=d.get("confidence", 0),` with:

```python
        scene_id=d.get("scene_id"), confidence=d.get("confidence", 0),
```

and replace the last line `residual=tuple(d.get("residual") or ()),` with:

```python
        residual=tuple(d.get("residual") or ()),
        passed_through=bool(d.get("passed_through")),
        scene=_scene_ref(d["scene"]) if d.get("scene") else None,
```

In `_outcome`, after `items_total=d.get("items_total", 0), rewritten=d.get("rewritten", 0),` add:

```python
        passed_through=d.get("passed_through", 0),
```

- [ ] **Step 4: Implement the recorder**

In `Recorder.__init__`, after `self._passthrough_count: int | None = None` add:

```python
        self._passed_count = 0
```

After `Recorder.passthrough_results`, add:

```python
    @_shielded
    def passed_through(self, items) -> None:
        # items: iterable of (ReleaseCandidate, SceneFingerprint | None,
        # MatchScore | None) returned to Whisparr unmodified. The scene and
        # score are the CLOSEST wanted scene, kept so the UI can say why the
        # item wasn't rewritten; None when nothing came close or nothing was
        # scored. matched stays False even at/above threshold: it means
        # "Scenehound rewrote it".
        for cand, scene, ms in items:
            self._cands.append(CandidateTrace(
                title=cand.title,
                guid=_sanitize(cand.guid),
                size=cand.size,
                seeders=cand.seeders,
                scene_id=scene.scene_id if scene is not None else None,
                confidence=ms.confidence if ms is not None else 0,
                strong_signals=ms.strong_signals if ms is not None else (),
                veto=ms.veto if ms is not None else None,
                detail=dict(ms.detail) if ms is not None else {},
                matched=False,
                rewritten_title=None,
                residual=ms.residual if ms is not None else (),
                passed_through=True,
                scene=SceneRef.from_scene(scene) if scene is not None else None,
            ))
            self._passed_count += 1
```

Replace `rss_summary`'s body:

```python
        self._kind = "rss"
        self._items_total = items_total
        self.scored(matched)
        self._rewritten = len(self._cands)
```

with:

```python
        self._kind = "rss"
        self._items_total = items_total
        matched = list(matched)
        self.scored(matched)
        # Not len(self._cands): that now holds passed-through rows as well.
        self._rewritten = len(matched)
```

In `commit`, replace:

```python
            outcome=Outcome(status=status, matched_count=matched_count,
                            items_total=self._items_total, rewritten=self._rewritten),
```

with:

```python
            outcome=Outcome(status=status, matched_count=matched_count,
                            items_total=self._items_total, rewritten=self._rewritten,
                            passed_through=self._passed_count),
```

In `NullRecorder`, after `def passthrough_results(self, count) -> None: ...` add:

```python
    def passed_through(self, items) -> None: ...
```

In `SessionStore.record_grab`, replace:

```python
            log.info("grab correlated session=%d kind=%s slug=%s title=%r",
                     s.session_id, s.kind, s.slug, release_title)
```

with:

```python
            log.info("grab correlated session=%d kind=%s slug=%s passed_through=%s "
                     "title=%r", s.session_id, s.kind, s.slug,
                     any(c.passed_through for c in matches), release_title)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 410 passed.

- [ ] **Step 7: Commit**

```bash
git add scenehound/observe.py tests/test_observe.py
git commit -m "$(cat <<'EOF'
feat: record passed-through items as candidate rows

CandidateTrace gains passed_through and the closest wanted scene;
scene_id is None when no wanted scene came close. Outcome counts the
items returned unchanged, so the UI can tell pruned rows from rows an
older build never recorded.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: List each passed-through RSS item once, under the latest poll

**Files:**
- Modify: `scenehound/observe.py` (`SessionStore.add` lines 272–275; new `SessionStore._prune_relisted`)
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: Task 2's `CandidateTrace.passed_through`, `Recorder.passed_through`, `Outcome.passed_through`.
- Produces: `SessionStore.add(session)` prunes older same-slug RSS sessions when `session.kind == "rss"`; `SessionStore._prune_relisted(new: SearchSession) -> None` (private).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observe.py`:

```python
# ---- one row per passed-through RSS item -----------------------------------
# The same feed items come back on every poll (HappyFappy's 25 span ~87 h).
# Each passed-through item is listed once, under the latest poll that
# returned it, so the newest poll per slug always lists the whole feed.

def _rss_poll(store, guids, *, slug="empornium", rewritten=()):
    rec = store.recorder(slug, 75, "")
    rec.passed_through([(_cand(g, f"Raw.{g}"), None, None) for g in guids])
    rec.rss_summary(len(guids) + len(rewritten),
                    [(_cand(g, f"Rel.{g}"), SCENE, _ms(90), f"Rewritten {g}")
                     for g in rewritten])
    rec.commit()


def _rows(store):
    """{session_id: [guid, ...]} in stored order."""
    return {s["session_id"]: [c["guid"] for c in s["candidates"]]
            for s in store.snapshot()["sessions"]}


def test_rss_poll_moves_relisted_rows_to_the_newest_poll():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b", "c"])
    _rss_poll(store, ["b", "c", "d"])
    assert _rows(store) == {2: ["b", "c", "d"], 1: ["a"]}
    older = store.snapshot()["sessions"][1]
    assert older["outcome"]["passed_through"] == 3   # capture-time count survives


def test_pruning_keeps_a_grabbed_row_through_later_polls():
    # Review focus 4.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    store.record_grab("Raw.a", "HASH1", 1000)        # lands on poll 1
    _rss_poll(store, ["a", "b"])
    _rss_poll(store, ["a", "b"])
    assert _rows(store) == {3: ["a", "b"], 2: [], 1: ["a"]}
    first = store.snapshot()["sessions"][2]
    assert first["outcome"]["grabs"][0]["grabbed_guid"] == "a"

    store.record_grab("Raw.a", "HASH2", 1000)        # a second grab: newest poll
    newest = store.snapshot()["sessions"][0]
    assert newest["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH2"


def test_pruning_never_touches_rewritten_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a"], rewritten=["r"])
    _rss_poll(store, ["a", "r"])       # r's scene left the wanted list
    assert _rows(store)[1] == ["r"]


def test_pruning_is_per_slug_and_rss_only():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "familytherapyxxx 26.07.26")
    rec.fallback("unparseable-query")
    rec.passthrough_results(1)
    rec.passed_through([(_cand("a", "Raw.a"), None, None)])
    rec.commit()                                   # 1: search passthrough
    _rss_poll(store, ["a"], slug="happyfappy")     # 2: other slug
    _rss_poll(store, ["a"], slug="empornium")      # 3: prunes nothing above
    assert _rows(store) == {3: ["a"], 2: ["a"], 1: ["a"]}


def test_an_empty_guid_never_prunes():
    # Review focus 5.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, [""])
    _rss_poll(store, [""])
    assert _rows(store) == {2: [""], 1: [""]}


def test_pruning_carries_the_outcome_by_reference():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    store.record_grab("Raw.a", "HASH1", 1000)
    _rss_poll(store, ["b"])                        # trims poll 1 (drops b)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    first = store.snapshot()["sessions"][1]
    assert first["outcome"]["grabs"][0]["imported"]["movie_id"] == 7


def test_a_pruning_failure_still_keeps_the_new_session(monkeypatch, caplog):
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a"])

    def boom(new):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(store, "_prune_relisted", boom)
    _rss_poll(store, ["a"])
    assert _rows(store) == {2: ["a"], 1: ["a"]}    # duplicates, nothing lost
    assert "failed (ignored)" in caplog.text


def test_restored_sessions_are_pruned_by_the_next_poll(tmp_path):
    # Review focus 2: a restart between polls.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    _rss_poll(restored, ["b"])
    assert _rows(restored) == {2: ["b"], 1: ["a"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q -k "prun or relisted or empty_guid or restored_sessions"`
Expected: FAIL. `test_rss_poll_moves_relisted_rows_to_the_newest_poll`, `test_pruning_keeps_a_grabbed_row_through_later_polls`, `test_pruning_never_touches_rewritten_rows` and `test_restored_sessions_are_pruned_by_the_next_poll` fail on the row sets; `test_a_pruning_failure_still_keeps_the_new_session` fails because `_prune_relisted` doesn't exist yet (`monkeypatch.setattr` raises `AttributeError`). `test_pruning_is_per_slug_and_rss_only`, `test_an_empty_guid_never_prunes` and `test_pruning_carries_the_outcome_by_reference` may already pass — they guard what pruning must not do.

- [ ] **Step 3: Implement the pruning**

In `SessionStore`, replace:

```python
    @_shielded
    def add(self, session: SearchSession) -> None:
        self._dirty = True
        self._sessions.appendleft(session)
```

with:

```python
    @_shielded
    def add(self, session: SearchSession) -> None:
        self._dirty = True
        self._sessions.appendleft(session)
        # Last, so a failure here (swallowed by the shield) still leaves the
        # new session in the ring: the worst case is duplicate rows.
        if session.kind == "rss":
            self._prune_relisted(session)

    def _prune_relisted(self, new: SearchSession) -> None:
        """List each passed-through RSS item once, under the latest poll that
        returned it. The newest poll per slug then always lists the whole
        current feed, so any item Whisparr can grab from it correlates however
        many polls it has sat there. A row a grab points at stays put so its
        badge survives; rewritten rows are never touched."""
        relisted = {c.guid for c in new.candidates if c.passed_through and c.guid}
        if not relisted:
            return
        # By index: the loop replaces elements of the deque it walks.
        for i in range(len(self._sessions)):
            s = self._sessions[i]
            if s is new or s.kind != "rss" or s.slug != new.slug:
                continue
            grabbed = {r.grabbed_guid for r in s.outcome.grabs if r.grabbed_guid}
            kept = tuple(c for c in s.candidates
                         if not (c.passed_through and c.guid in relisted
                                 and c.guid not in grabbed))
            if len(kept) != len(s.candidates):
                # Outcome travels by reference: grabs stamped before (and
                # imports stamped after) the trim stay on this session.
                self._sessions[i] = dataclasses.replace(s, candidates=kept)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_observe.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 418 passed.

- [ ] **Step 6: Commit**

```bash
git add scenehound/observe.py tests/test_observe.py
git commit -m "$(cat <<'EOF'
feat: list each passed-through RSS item once, under the latest poll

The same feed items return on every poll (HappyFappy's 25 span ~87 h),
so an RSS poll now takes over the passed-through rows it re-lists from
the slug's older polls. Grabbed rows and rewritten rows stay put.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Capture passed-through items in `_rss_mode` and `_passthrough`

**Files:**
- Modify: `scenehound/api.py` (`_passthrough` lines 71–84; `_rss_mode` lines 165–203; new `_best_scene` and `_record_passthrough`)
- Test: `tests/test_api.py` (edit `test_rss_records_summary` lines 299–307; append new tests)

**Interfaces:**
- Consumes: Task 2's `Recorder.passed_through(items)` / `NullRecorder.passed_through`, `Outcome.passed_through`, `CandidateTrace.passed_through` / `.scene` / nullable `scene_id`.
- Produces:
  - `_best_scene(index: WantedIndex, title: str, threshold: int, skew: int) -> tuple[SceneFingerprint, MatchScore] | None`
  - `_record_passthrough(state: AppState, results: list[ReleaseCandidate], rec) -> None`
  - The RSS no-index note text `wanted list not loaded — items passed through unscored`.

- [ ] **Step 1: Update the RSS summary test for the new rows**

In `tests/test_api.py`, in `test_rss_records_summary`, replace:

```python
    assert len(s["candidates"]) == 1
    assert s["candidates"][0]["rewritten_title"] is not None
```

with:

```python
    rewritten = [c for c in s["candidates"] if not c["passed_through"]]
    assert len(rewritten) == 1
    assert rewritten[0]["rewritten_title"] is not None
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api.py`:

```python
# ---- passed-through items -----------------------------------------------------
import json
from datetime import date

import pytest

from scenehound.api import _best_scene
from scenehound.models import SceneFingerprint
from scenehound.wanted_index import WantedIndex

# Scores below were computed with the real matcher against conftest's SCENE
# (That Fetish Girl / TFG, 2026-07-07, "Latex Worship Session", Jane Doe).
FEED_NEAR_MISS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.Jane.Doe.Beach.Day.720p</title>
      <guid>https://tracker.example/torrents.php?action=download&amp;id=42&amp;authkey=AUTHKEY123&amp;torrent_pass=PASS456</guid>
      <link>http://p/dl/42</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def test_best_scene_keeps_the_first_scene_on_a_rewrite_tie():
    # Both clear the threshold at 100; 200 has MORE strong signals. Today's
    # RSS rule is first-wins on a confidence tie, and must stay so.
    low = SceneFingerprint(100, "That Fetish Girl", (), date(2026, 7, 7),
                           "Latex Worship Session", ())
    high = SceneFingerprint(200, "That Fetish Girl", (), date(2026, 7, 7),
                            "Latex Worship Session", ("Jane Doe",))
    scene, ms = _best_scene(WantedIndex([low, high]),
                            "ThatFetishGirl.2026-07-07.Latex.Worship.Session.[Jane.Doe].1080p",
                            75, 3)
    assert (scene.scene_id, ms.confidence) == (100, 100)


def test_best_scene_prefers_more_strong_signals_below_threshold():
    # Both vetoed to 0: 100 is a site-mismatch with only the date agreeing,
    # 200 a foreign-title with site AND date agreeing -- the nearer miss.
    other = SceneFingerprint(100, "Other Studio", (), date(2026, 7, 7), "Beach Day", ())
    tfg = SceneFingerprint(200, "That Fetish Girl", (), date(2026, 7, 7),
                           "Latex Worship Session", ())
    scene, ms = _best_scene(WantedIndex([other, tfg]),
                            "ThatFetishGirl.2026-07-07.Totally.Different.Film.1080p",
                            75, 3)
    assert scene.scene_id == 200
    assert ms.veto == "foreign-title"


def test_best_scene_is_none_when_no_wanted_scene_is_a_candidate():
    scene = SceneFingerprint(7, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane Doe", "Mary Major"))
    assert _best_scene(WantedIndex([scene]), "Unrelated.Studio.Thing.720p", 75, 3) is None


def test_rss_records_passed_through_items(app_with_store, store):
    _get(app_with_store)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 1
    assert s["outcome"]["passed_through"] == 1
    [pt] = [c for c in s["candidates"] if c["passed_through"]]
    assert pt["title"] == "Unrelated.Studio.Thing.720p"
    assert pt["scene_id"] is None and pt["scene"] is None
    assert pt["matched"] is False


def test_rss_records_the_closest_scene_below_threshold(make_app, store):
    _get(make_app(store=store, feed=FEED_NEAR_MISS))
    s = store.snapshot()["sessions"][0]
    [pt] = s["candidates"]
    assert pt["passed_through"] is True
    assert pt["confidence"] == 70
    assert set(pt["strong_signals"]) == {"site", "performer"}
    assert pt["scene_id"] == 7
    assert pt["scene"]["title"] == "Latex Worship Session"
    assert "AUTHKEY123" not in json.dumps(s) and "PASS456" not in json.dumps(s)


def test_rss_without_index_records_every_item_unscored(make_app, store):
    _get(make_app(store=store, with_index=False))
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 0
    assert s["outcome"]["passed_through"] == 2
    assert all(c["passed_through"] and c["scene_id"] is None for c in s["candidates"])
    assert "wanted list not loaded — items passed through unscored" in s["notes"]


def test_unparseable_passthrough_records_scored_results(app_with_store, store):
    r = _get(app_with_store, q="just some words")
    # The response is Prowlarr's, verbatim.
    assert set(titles(r)) == {"TFG.26.07.07.Latex.Worship.Session.1080p",
                              "Unrelated.Studio.Thing.720p"}
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["matched_count"] == 2
    assert s["outcome"]["passed_through"] == 2
    hit = next(c for c in s["candidates"]
               if c["title"] == "TFG.26.07.07.Latex.Worship.Session.1080p")
    assert hit["confidence"] == 100                    # would have matched...
    assert hit["matched"] is False                     # ...but went back unchanged
    assert hit["passed_through"] is True and hit["scene_id"] == 7


def test_no_index_passthrough_records_unscored_results(make_app, store):
    _get(make_app(store=store, with_index=False), q=SEARCH_Q)
    s = store.snapshot()["sessions"][0]
    assert s["fallback_reason"] == "no-index"
    assert len(s["candidates"]) == 2
    assert all(c["scene_id"] is None and c["confidence"] == 0 for c in s["candidates"])


def test_passthrough_scores_nothing_without_a_store(make_app, monkeypatch):
    calls = []
    monkeypatch.setattr("scenehound.api._best_scene",
                        lambda *a, **k: calls.append(a) or None)
    _get(make_app(), q="just some words")
    assert calls == []


def test_passthrough_scoring_failure_still_returns_the_feed(app_with_store, store,
                                                            monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("scoring exploded")

    monkeypatch.setattr("scenehound.api._best_scene", boom)
    r = _get(app_with_store, q="just some words")
    assert len(titles(r)) == 2
    s = store.snapshot()["sessions"][0]
    assert s["candidates"] == []
    assert s["outcome"]["matched_count"] == 2
    assert "passthrough scoring for the UI failed" in caplog.text


@pytest.mark.parametrize("q,with_index", [
    (None, True),                          # RSS
    (None, False),                         # RSS, no index
    ("just some words", True),             # unparseable-query
    ("Unknown Studio 01.01.2020", True),   # scene-unresolved
    (SEARCH_Q, False),                     # no-index
])
def test_ui_on_or_off_returns_identical_bytes(make_app, q, with_index):
    from scenehound.observe import SessionStore
    st = SessionStore(max_sessions=50, max_candidates=200)
    plain = _get(make_app(with_index=with_index), q=q)
    traced = _get(make_app(store=st, with_index=with_index), q=q)
    assert plain.content == traced.content
    assert st.snapshot()["sessions"][0]["candidates"]    # and it did record
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`
Expected: FAIL at collection with `ImportError: cannot import name '_best_scene' from 'scenehound.api'`.

- [ ] **Step 4: Implement `_best_scene` and `_record_passthrough`**

In `scenehound/api.py`, after the `_Scored` dataclass, add:

```python
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
```

- [ ] **Step 5: Record from `_passthrough`**

In `_passthrough`, replace:

```python
    results = await state.prowlarr.search(indexer.prowlarr_id, query, cats)
    rec.passthrough_results(len(results))
    log.info("search slug=%s mode=passthrough q=%r results=%d",
             indexer.slug, query, len(results))
```

with:

```python
    results = await state.prowlarr.search(indexer.prowlarr_id, query, cats)
    rec.passthrough_results(len(results))
    # Recorded so a Whisparr grab of one correlates. Scoring is new work on
    # this path, so it only runs when there is a UI to show it.
    if state.store is not None:
        _record_passthrough(state, results, rec)
    log.info("search slug=%s mode=passthrough q=%r results=%d",
             indexer.slug, query, len(results))
```

- [ ] **Step 6: Rewrite `_rss_mode` around `_best_scene`**

Replace the whole of `_rss_mode` with:

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`
Expected: PASS (all of `test_api.py`, including the unchanged `test_rss_mode_rewrites_to_best_scene_not_first`).

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 433 passed.

- [ ] **Step 9: Commit**

```bash
git add scenehound/api.py tests/test_api.py
git commit -m "$(cat <<'EOF'
feat: record items RSS and search passthrough return unchanged

_best_scene keeps the RSS rewrite choice (highest confidence, first on
ties) and otherwise finds the closest wanted scene, so a passed-through
row can say why it wasn't rewritten. Search-passthrough results are
scored only when the UI is on, and a scoring failure never touches the
response.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Render passed-through rows in `/ui`

**Files:**
- Modify: `scenehound/static/ui.html` (CSS block lines 8–63; `whyLines` lines 124–149; `badges` lines 174–183; `candidates` lines 217–238; `outcome` lines 240–266; `render` lines 268–291)
- Modify: `README.md` (Web UI section, the **Candidates** bullet)
- Test: `tests/test_ui_api.py` (`test_ui_page_has_app_markers`)

**Interfaces:**
- Consumes: the `/ui/api/sessions` JSON shape from Tasks 2–4: `candidate.passed_through`, `candidate.scene` (`{scene_id, site, date, title, performers}` or `null`), `candidate.scene_id` (may be `null`), `outcome.passed_through`.
- Produces: JS functions `closestLine(c)`, `verdict(c, threshold)`, `grabsByGuid(s)`, `candidateRow(c, s, byGuid)`, `passedThrough(s, open)`; `render()` keys open `<details>` by `data-key`.

- [ ] **Step 1: Write the failing marker test**

In `tests/test_ui_api.py`, in `test_ui_page_has_app_markers`, replace the marker tuple:

```python
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid",
                   "grabPills", "o.grabs"):
```

with:

```python
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid",
                   "grabPills", "o.grabs", "passedThrough", "c.passed_through",
                   "o.passed_through", "dataset.key",
                   "not correlated to any recorded search or RSS poll"):
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ui_api.py -q`
Expected: FAIL with `AssertionError: passedThrough`.

- [ ] **Step 3: Add the block's CSS**

In `ui.html`, after the line `.ladder li { margin: .15rem 0; }` add:

```css
.pt { margin-top: .8rem; border: 1px dashed #3a3f4a; border-radius: 6px; }
.pt > summary { padding: .35rem .7rem; cursor: pointer; color: #9aa3b3; font-size: .85rem; }
.pt > table { margin: 0 0 .3rem; }
.pt-line { margin-top: .8rem; font-size: .85rem; }
```

- [ ] **Step 4: Teach `whyLines` about passed-through rows**

Replace the whole of `whyLines` with:

```js
function closestLine(c) {
  const sc = c.scene;
  return sc
    ? `Closest wanted scene: <b>${esc(sc.site)} — ${esc(sc.title)}</b> (${esc(sc.date)})`
    : `<span class="muted">No wanted scene shares a date or name with this release.</span>`;
}

function verdict(c, threshold) {
  const hit = c.confidence >= threshold;
  if (!c.passed_through) return hit
    ? `<b>${c.confidence}</b> ≥ threshold <b>${threshold}</b> → <b>matched</b>`
    : `<b>${c.confidence}</b> &lt; threshold <b>${threshold}</b> → not matched`;
  // Only a search passthrough can return a row that clears the threshold:
  // RSS would have rewritten it.
  return hit
    ? `<span class="warn"><b>${c.confidence}</b> ≥ threshold <b>${threshold}</b> → would have matched, but this query path returns results unchanged</span>`
    : `<b>${c.confidence}</b> &lt; threshold <b>${threshold}</b> → not rewritten, passed through unchanged`;
}

function whyLines(c, threshold) {
  const out = [];
  if (c.passed_through) {
    out.push(closestLine(c));
    if (!c.scene) return out;   // nothing came close, so nothing to explain
  }
  if (c.veto) {
    out.push(`<span class="no">✗ ${esc(VETO_TEXT[c.veto] || "Rejected: " + c.veto)}</span>`);
    if (c.residual && c.residual.length)
      out.push(`<span class="warn">Unexplained words: ${esc(c.residual.join(", "))}</span>`);
    return out;
  }
  for (const sig of c.strong_signals) {
    const pts = c.detail[sig] != null ? ` (+${Math.round(c.detail[sig])})` : "";
    out.push(`<span class="ok">✓ ${esc(STRONG_TEXT[sig] || sig)}${pts}</span>`);
  }
  if (c.detail.date_skew_days != null)
    out.push(`<span class="warn">⚠ Release date is ${c.detail.date_skew_days} day(s) off the scene's — forgiven: ${c.strong_signals.length} other strong signals.</span>`);
  if (c.detail.date_secondary_reading != null)
    out.push(`<span class="warn">⚠ Date matches only via an alternate reading of an ambiguous format (e.g. dd-mm-yy vs yy-mm-dd) — not counted as a strong signal.</span>`);
  if (!c.strong_signals.includes("title") && c.detail.title != null)
    out.push(`Title is similar (+${c.detail.title.toFixed(1)})`);
  if (c.strong_signals.length === 1)
    out.push(`<span class="warn">⚠ Needs two strong signals to match — only one found, so confidence is capped at 65.</span>`);
  out.push(`Confidence ${verdict(c, threshold)}`);
  return out;
}
```

- [ ] **Step 5: Keep the kind pill beside grab pills**

Replace the whole of `badges` with:

```js
function badges(s) {
  const o = s.outcome;
  const g = grabPills(o);
  // A grab must never hide that this card only passed items through.
  if (s.kind === 'rss') return [...g, ['b-rss', `RSS: ${o.rewritten} rewritten of ${o.items_total}`]];
  if (s.kind === 'passthrough') return [...g, ['b-pass', `Passthrough (${o.matched_count})`]];
  if (g.length) return g;
  if (o.status === 'error') return [['b-error', 'Error']];
  if (o.status === 'matched') return [['b-matched', `Matched (${o.matched_count})`]];
  return [['b-empty', 'No matches']];
}
```

- [ ] **Step 6: Split out rows; add the passed-through block**

Replace the whole of `candidates` with:

```js
const CAND_HEAD = `<tr><th>Release title</th><th>Size</th><th>Seeders</th><th>Confidence</th><th></th></tr>`;

function grabsByGuid(s) {
  const m = {};
  for (const rec of (s.outcome.grabs || []))
    if (rec.grabbed_guid) m[rec.grabbed_guid] = rec;
  return m;
}

function candidateRow(c, s, byGuid) {
  // Each grabbed row badges with ITS OWN grab's state.
  const gb = byGuid[c.guid] ? grabBadge(byGuid[c.guid]) : null;
  return `<tr class="${c.matched ? "conf-hit" : ""}">` +
    `<td>${esc(c.title)}${gb ? ` <span class="badge ${gb[0]}">${esc(gb[1])}</span>` : ""}${c.rewritten_title
        ? `<div class="muted">→ returned as: ${esc(c.rewritten_title)}</div>` : ""}` +
    `<div class="why">${whyLines(c, s.threshold).join("<br>")}</div></td>` +
    `<td class="num">${fmtSize(c.size)}</td><td class="num">${c.seeders ?? ""}</td>` +
    `<td class="num">${c.confidence} <span class="confbar" style="width:${c.confidence * 0.6}px"></span></td>` +
    `<td>${c.matched ? "✓" : ""}</td></tr>`;
}

function candidates(s) {
  // Passed-through rows render in their own collapsed block (passedThrough).
  const own = s.candidates.filter(c => !c.passed_through);
  if (!own.length) return s.kind === "search"
    ? `<h3>Candidates</h3><div class="muted">Prowlarr returned nothing for any query variant.</div>` : "";
  const byGuid = grabsByGuid(s);
  let h = `<h3>Candidates (${own.length}${
    s.dropped_candidates ? ` shown, +${s.dropped_candidates} more dropped` : ""})</h3>`;
  h += `<table>${CAND_HEAD}`;
  for (const c of own) h += candidateRow(c, s, byGuid);
  return h + `</table>`;
}

function passedThrough(s, open) {
  const rows = s.candidates.filter(c => c.passed_through);
  // Only RSS rows move: an RSS poll takes over the rows it re-lists from the
  // slug's older polls. Pre-feature sessions count 0 and claim nothing.
  const later = s.kind === "rss" ? Math.max(0, (s.outcome.passed_through || 0) - rows.length) : 0;
  if (!rows.length) return later
    ? `<div class="muted pt-line">Passed through unchanged: all ${later} are listed under later polls.</div>` : "";
  const byGuid = grabsByGuid(s);
  // The block starts collapsed, so its summary carries any grab inside it.
  const seen = new Set();
  let grabbed = "";
  for (const c of rows) {
    if (!byGuid[c.guid]) continue;
    const [cls, label] = grabBadge(byGuid[c.guid]);
    if (seen.has(cls)) continue;
    seen.add(cls);
    grabbed += ` <span class="badge ${cls}">${esc(label)}</span>`;
  }
  const key = `${s.session_id}-pt`;
  let h = `<details class="pt" data-key="${key}"${open.has(key) ? " open" : ""}>` +
    `<summary>Passed through — not rewritten (${rows.length})` +
    (later ? ` · ${later} more listed under later polls` : "") +
    (s.kind !== "rss" && s.dropped_candidates ? ` · +${s.dropped_candidates} more dropped` : "") +
    `${grabbed}</summary><table>${CAND_HEAD}`;
  for (const c of rows) h += candidateRow(c, s, byGuid);
  return h + `</table></details>`;
}
```

- [ ] **Step 7: Say it in the outcome ladder**

In `outcome`, replace:

```js
  if (s.kind === "rss")
    h += `<li>RSS sync: ${o.items_total} items from Prowlarr, ${o.rewritten} rewritten to canonical titles.</li>`;
```

with:

```js
  if (s.kind === "rss")
    h += `<li>RSS sync: ${o.items_total} items from Prowlarr, ${o.rewritten} rewritten to canonical titles` +
      (o.passed_through ? `, ${o.passed_through} passed through unchanged` : "") + `.</li>`;
```

and replace:

```js
    h += `<li>✓ Whisparr grabbed <code>${esc(rec.grab.release_title)}</code>` +
      (gc && gc.title !== rec.grab.release_title
        ? ` — candidate <code>${esc(gc.title)}</code>` : "") +
      ` at ${fmtTime(rec.grab.at)}.</li>`;
```

with:

```js
    h += `<li>✓ Whisparr grabbed <code>${esc(rec.grab.release_title)}</code>` +
      (gc && gc.title !== rec.grab.release_title
        ? ` — candidate <code>${esc(gc.title)}</code>` : "") +
      (gc && gc.passed_through
        ? ` — passed through unchanged (Scenehound's best: ${gc.confidence}${gc.veto ? ", " + esc(gc.veto) : ""})` : "") +
      ` at ${fmtTime(rec.grab.at)}.</li>`;
```

- [ ] **Step 8: Key open state by `data-key`; wire the block in; fix the strip copy**

In `render`, replace:

```js
  const open = new Set([...document.querySelectorAll("details[open]")].map(d => d.dataset.sid));
```

with:

```js
  // Cards and their nested passed-through blocks both keep their open state
  // across the 4 s re-render, each by its own key.
  const open = new Set([...document.querySelectorAll("details[open]")].map(d => d.dataset.key));
```

replace:

```js
    ` <span class="muted">not correlated to a recorded search — ${fmtTime(u.grab.at)}</span>` +
```

with:

```js
    ` <span class="muted">not correlated to any recorded search or RSS poll — ${fmtTime(u.grab.at)}</span>` +
```

and replace:

```js
    return `<details class="card" data-sid="${s.session_id}" ${open.has(String(s.session_id)) ? "open" : ""}>` +
```

with:

```js
    return `<details class="card" data-key="${s.session_id}" ${open.has(String(s.session_id)) ? "open" : ""}>` +
```

and replace:

```js
      `<div class="body">${queryChain(s)}${candidates(s)}${outcome(s)}</div></details>`;
```

with:

```js
      `<div class="body">${queryChain(s)}${candidates(s)}${passedThrough(s, open)}${outcome(s)}</div></details>`;
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ui_api.py -q`
Expected: PASS.

- [ ] **Step 10: Document it in the README**

In `README.md`, in the Web UI section's **Candidates** bullet, replace:

```
  A release that contains the whole scene title but adds words of its own is
  rejected as a longer-titled scene of the same studio, and the UI names the
  words it could not explain.
```

with:

```
  A release that contains the whole scene title but adds words of its own is
  rejected as a longer-titled scene of the same studio, and the UI names the
  words it could not explain. Releases Scenehound returns to Whisparr
  unchanged — RSS items it didn't rewrite, and the results of searches it
  couldn't parse — are listed in a collapsed **Passed through** block with the
  closest wanted scene and why it fell short, and are badged there when
  Whisparr grabs one.
```

- [ ] **Step 11: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 433 passed.

- [ ] **Step 12: Commit**

```bash
git add scenehound/static/ui.html tests/test_ui_api.py README.md
git commit -m "$(cat <<'EOF'
feat: show passed-through items in /ui, collapsed per session

Each card gets a collapsed "Passed through — not rewritten" block naming
the closest wanted scene; a grab badges its row and the block's summary.
RSS and passthrough cards keep their kind pill beside grab pills so a
card never implies Scenehound rewrote what it only passed through.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Headless render verification

The single-file UI has no JS test framework. The proven method (PRs #19 and #22) renders the real page against a real `SessionStore` stub with the playwright-cache `chrome-headless-shell` and checks the dumped DOM. This task is evidence-gathering and changes no repo files. If it exposes a rendering bug, fix `ui.html` (or `observe.py`), re-run the full suite, re-run this check, and add a `fix:` commit.

Wherever `<scratchpad>` appears below, substitute the scratchpad directory listed in your own system prompt (never a path inside the repo).

**Files:**
- Create (scratch, NOT committed): `<scratchpad>/serve_stub.py`, `<scratchpad>/check_dom.py`
- Read-only: `scenehound/static/ui.html`, `scenehound/ui_api.py`

**Interfaces:**
- Consumes: Tasks 2–5 end to end (`Recorder.passed_through`, pruning in `SessionStore.add`, the rendered page).
- Produces: DOM evidence for each expectation in Step 3.

- [ ] **Step 1: Write the stub server**

Write `<scratchpad>/serve_stub.py`:

```python
"""Serve /ui (and /uitest, the same page plus an open-state probe) against a
stubbed SessionStore exercising every passed-through rendering case."""
from datetime import date

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from scenehound.api import AppState, IndexHolder
from scenehound.config import Config, IndexerConfig, ServiceConfig
from scenehound.matcher import MatchScore
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.observe import SessionStore
from scenehound.rate_limiter import TokenBucket
from scenehound.ui_api import _PAGE, ui_router
from scenehound.wanted_index import WantedIndex

cfg = Config(
    whisparr=ServiceConfig("http://w:6969", "wk"),
    prowlarr=ServiceConfig("http://p:9696", "pk"),
    indexers=(IndexerConfig("empornium", 12),),
    api_key="shk",
)
scene = SceneFingerprint(7, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                         "Latex Worship Session", ("Jane Doe",))


def _cand(guid, title, size=1000):
    return ReleaseCandidate(title=title, guid=guid, link="http://p/dl",
                            size=size, seeders=5)


near = MatchScore(70, ("site", "performer"), None, {"site": 35.0, "performer": 35.0})
veto = MatchScore(0, (), "date-mismatch", {"date": 0.0})
hit = MatchScore(100, ("date", "site", "title"), None,
                 {"date": 40.0, "site": 35.0, "title": 25.0})

store = SessionStore(max_sessions=20, max_candidates=200)


def rss_poll(items):
    rec = store.recorder("empornium", 75, "")
    rec.passed_through(items)
    rec.rss_summary(len(items), [])
    rec.commit()


# 1. Pre-feature RSS session: counts only, no rows, passed_through 0.
rec = store.recorder("empornium", 75, "")
rec.rss_summary(25, [])
rec.commit()
# 2. Keeps P0 after pruning -> "(1) · 1 more listed under later polls".
rss_poll([(_cand("p0", "Raw.P0"), scene, veto), (_cand("p1", "Raw.P1"), None, None)])
# 3. Everything re-listed later -> "all 2 are listed under later polls".
rss_poll([(_cand("p1", "Raw.P1"), None, None), (_cand("p2", "Raw.P2"), scene, near)])
# 4. Newest RSS poll: P1, P2, P3 -- P3 grabbed.
rss_poll([(_cand("p1", "Raw.P1"), None, None), (_cand("p2", "Raw.P2"), scene, near),
          (_cand("p3", "Raw.P3"), scene, near)])
store.record_grab("Raw.P3", "HASH-P3", 1000)
# 5. Search passthrough whose one result would have matched.
rec = store.recorder("empornium", 75, "familytherapyxxx 26.07.26")
rec.query(None, ())
rec.fallback("unparseable-query")
rec.passthrough_results(1)
rec.passed_through([(_cand("s1", "TFG.26.07.07.Latex.Worship.Session.1080p"), scene, hit)])
rec.commit()

PROBE = """<script>
setTimeout(() => {
  const pt = document.querySelector("details.pt");
  const key = pt.dataset.key;
  pt.open = true;
  setTimeout(() => {
    const again = document.querySelector(`details.pt[data-key="${key}"]`);
    document.body.dataset.ptOpenAfterPoll = String(!!(again && again.open));
    document.body.dataset.ptSameNode = String(again === pt);
  }, 5000);
}, 1500);
</script>"""

holder = IndexHolder()
holder.set(WantedIndex([]))
app = FastAPI()
app.include_router(ui_router)


@app.get("/uitest")
async def uitest() -> HTMLResponse:
    return HTMLResponse(_PAGE.replace("</body>", PROBE + "</body>"))


app.state.scenehound = AppState(
    config=cfg, prowlarr=None, index_holder=holder,
    buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    store=store,
)
uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
```

- [ ] **Step 2: Write the DOM checker**

Write `<scratchpad>/check_dom.py`:

```python
"""Assert on the rendered #sessions subtree only: --dump-dom also emits the
<script> source, whose template literals contain the same strings."""
import re
import sys
from pathlib import Path

d = Path(sys.argv[1])
dom = (d / "dom.html").read_text()
probe = (d / "probe.html").read_text()
sessions = dom.split('<div id="sessions">', 1)[1].split("<script>", 1)[0]

checks = {
    "later-polls lines (rss0 partial + rss1 all)": sessions.count("listed under later polls") == 2,
    "rss1: all 2 moved": sessions.count("Passed through unchanged: all 2 are listed under later polls.") == 1,
    "rss0: 1 more": sessions.count("· 1 more listed under later polls") == 1,
    "pre-feature card claims nothing": "all 25 are listed" not in sessions and "25 more listed" not in sessions,
    "3 collapsed pt blocks": len(re.findall(r'<details class="pt" data-key="[^"]+">', sessions)) == 3,
    "no pt block open": len(re.findall(r'<details class="pt"[^>]* open', sessions)) == 0,
    "would-have-matched row": sessions.count("would have matched, but this query path returns results unchanged") == 1,
    "ladder names the passthrough grab": sessions.count("passed through unchanged (Scenehound's best: 70)") == 1,
    "no-scene row text": sessions.count("No wanted scene shares a date or name with this release.") == 1,
    "closest-scene rows": sessions.count("Closest wanted scene:") == 4,
    "grab pill beside RSS pill": re.search(r"Grabbed</span> <span class=\"badge b-rss\">RSS: 0 rewritten of 3<", sessions) is not None,
    "pt summary carries the grab badge": re.search(r"Passed through — not rewritten \(3\) <span class=\"badge b-grabbed\">Grabbed</span></summary>", sessions) is not None,
    "no unmatched strip": "not correlated to" not in dom.split('<div id="grabs">', 1)[1].split('<div id="sessions">', 1)[0],
    "open state survives a re-render": 'data-pt-open-after-poll="true"' in probe,
    "…on a freshly rendered node": 'data-pt-same-node="false"' in probe,
}
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(checks.values()) else 1)
```

- [ ] **Step 3: Serve, dump both pages, check**

```bash
cd /Users/jamesking/VS/Scenehound
.venv/bin/python <scratchpad>/serve_stub.py & SRV=$!
sleep 2
SHELL_BIN=$(ls -d "$HOME/Library/Caches/ms-playwright"/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell | sort -V | tail -1)
"$SHELL_BIN" --headless --disable-gpu --no-sandbox --virtual-time-budget=8000 \
  --dump-dom "http://127.0.0.1:8765/ui?apikey=shk" > <scratchpad>/dom.html
"$SHELL_BIN" --headless --disable-gpu --no-sandbox --virtual-time-budget=9000 \
  --dump-dom "http://127.0.0.1:8765/uitest?apikey=shk" > <scratchpad>/probe.html
kill $SRV
.venv/bin/python <scratchpad>/check_dom.py <scratchpad>
```

Expected: every line prints `PASS`, and the checker exits 0.

- [ ] **Step 4: Record the evidence**

Paste the checker's output into the task report. If a line prints `FAIL`, inspect `<scratchpad>/dom.html` around the expectation. A genuine rendering bug means: fix `ui.html` or `observe.py`, re-run the full suite, re-run Step 3, and commit the fix as `fix: …` with the trailer. A checker that mis-parses the DOM gets fixed in the checker, not in the product.

---

### Task 7: Final verification and PR

**Files:** none (git and gh only).

**Interfaces:**
- Consumes: all prior tasks' commits on `feat/record-passthrough-items`.
- Produces: an open PR against `main` (not merged).

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 433 passed, 0 failures.

- [ ] **Step 2: Confirm the isolation rule still holds**

Run: `grep -nE "^(from|import) " scenehound/observe.py`
Expected: only stdlib imports and `from scenehound.models import SceneFingerprint` — nothing from `api`, `import_completer`, `import_api`, or `fastapi`.

- [ ] **Step 3: Push and open the PR (do NOT merge)**

```bash
git push -u origin feat/record-passthrough-items
gh pr create --title "feat: record passed-through items so Whisparr's grabs of them correlate" --body "$(cat <<'EOF'
## Summary

Whisparr auto-grabs RSS items Scenehound passed through unchanged, and those grabs filled `/ui`'s unmatched strip because Scenehound never recorded the items. This keeps passing them through, and also records them.

- **RSS:** items below threshold are recorded with their closest wanted scene and score. Each item is listed once, under the latest poll that returned it, so the newest poll per slug always lists the whole feed.
- **Search passthrough** (`unparseable-query`, `scene-unresolved`, `no-index`): results are recorded, and scored when the UI is on. A result that would have matched is shown as such but still returned unchanged.
- **UI:** a collapsed "Passed through — not rewritten" block per card; a grab badges its row and the block's summary; RSS/passthrough cards keep their kind pill beside grab pills.
- **Security fix:** both live trackers' guids carry `authkey=` and `torrent_pass=`, which were stored unredacted in `ui-sessions.json` and served by `/ui/api/sessions`. Both are now redacted, and existing state files are scrubbed on load and rewritten on the next flush.

Response bytes are unchanged with the UI on or off (tested for RSS and all three passthrough fallbacks). The RSS rewrite choice is unchanged (tested on a confidence tie).

Spec: `docs/superpowers/specs/2026-09-28-record-passthrough-items-design.md`
Plan: `docs/superpowers/plans/2026-09-28-record-passthrough-items.md`

## Evidence behind the storage choice

Measured live on 2026-09-28: 25 items per RSS poll, every 30.5 min per slug. Empornium turns over ~2–3 items per poll; HappyFappy's 25 items span ~87 h. Listing every item in every poll would store ~1,050 rows (~680 KB, shipped on every 4 s `/ui` poll); listing each item once stores ~95 (~60 KB).

## Test plan

- [x] `pytest -q`: 433 passed
- [x] Headless DOM check of the rendered page against a stub store (collapsed blocks, "later polls" lines, pre-feature sessions claim nothing, grab badges, would-have-matched row, open state survives a re-render)
- [ ] Deploy, then confirm new Whisparr RSS grabs of raw titles show as grabs on passed-through rows in RSS sessions, not in the unmatched strip
- [ ] `/ui/api/sessions` stays in the tens of KB
- [ ] After restart, `ui-sessions.json` holds no unredacted `authkey=` or `torrent_pass=`

No version bump here: release as `chore: release 0.7.0` after merge and the live check.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report**

Report the PR URL and the final test count. Mention that a pre-merge live test is available with `gh workflow run ci.yml --ref feat/record-passthrough-items`, which publishes `ghcr.io/espionage9248/scenehound:feat-record-passthrough-items` — the user decides whether to use it.
