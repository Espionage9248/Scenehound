# Date-Skew Forgiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A release date 2–`date_skew_days` days off the scene's date is forgiven when the match clears the two-strong-signal rule without the date; otherwise the `date-mismatch` veto stands exactly as today.

**Architecture:** The forgiveness decision lives entirely in `scenehound/matcher.py::score()`, which gains a `date_skew_days: int = 3` parameter. A new `matching.date_skew_days` config field is threaded to the three `score()` call sites (torznab search, torznab RSS, import completer). The UI trace already receives the full `detail` dict, so a `detail["date_skew_days"]` metadata entry (excluded from the point total) surfaces forgiveness with only a JS rendering change.

**Tech Stack:** Python 3.12+ (frozen dataclasses, pure functions), pytest (run as `.venv/bin/python -m pytest`), vanilla-JS single-file UI (`scenehound/static/ui.html`).

**Spec:** `docs/superpowers/specs/2026-07-13-date-skew-forgiveness-design.md`

## Global Constraints

- Default skew window is **3** — must be identical in `score()`'s parameter default, `MatchingConfig.date_skew_days`, `load_config`, and the `match_pack`/`_match_one`/`ImportCompleter` pass-through defaults.
- The veto string stays exactly `"date-mismatch"`; no new veto kind.
- The trace metadata key is exactly `"date_skew_days"` (a float in `MatchScore.detail`); it must NOT contribute to the confidence total.
- Env var is exactly `SCENEHOUND_DATE_SKEW_DAYS`; yaml key `matching.date_skew_days`.
- `matcher.py` stays pure (no config import); callers pass the window in.
- Setting the window to 1 (or 0) must restore today's behaviour bit-for-bit.
- Out of scope: `wanted_index.py`, `query_planner.py`, importer all-or-nothing logic, `normalize.py`.

---

### Task 1: Matcher — forgiveness rule in `score()`

**Files:**
- Modify: `scenehound/matcher.py` (docstring; `score()` signature; date block at lines 112–119; total/return block at lines 156–159)
- Test: `tests/test_matcher.py`
- Test: `tests/fixtures/corpus.yaml` (append the production mismatch)

**Interfaces:**
- Consumes: nothing new.
- Produces: `score(scene, title, other_sites=frozenset(), date_skew_days: int = 3) -> MatchScore`. Forgiven matches have `veto is None`, no `"date"` in `strong_signals`, and `detail["date_skew_days"] == float(days_off)`. Later tasks rely on the keyword name `date_skew_days`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matcher.py` (module already imports `date`, `SINGLE_SIGNAL_CAP`, `MatchScore`, `score`, `SceneFingerprint`, and defines `SCENE` with site "That Fetish Girl"/alias "TFG", date 2026-07-07, title "Latex Worship Session", performers Jane Doe + Mary Major):

```python
# --- date-skew forgiveness (uploader-stamped dates a few days off) ---

FORGIVE_SCENE = SceneFingerprint(
    scene_id=20,
    site="Household Fantasy",
    site_aliases=(),
    date=date(2026, 7, 7),
    title="Big Titty Step-Sistinder Match",
    performers=("Zarina Noir",),
)


def test_skewed_date_forgiven_with_two_strong_signals():
    # Production mismatch 2026-07-13: uploader stamped 07-05 for the 07-07 scene.
    s = score(
        FORGIVE_SCENE,
        "[ScottStark-HouseholdFantasy] Zarina Noir - "
        "Big Titty Step Sister Tinder Match (2026-07-05) [1080p]",
    )
    assert s.veto is None
    assert {"site", "performer"} <= set(s.strong_signals)
    assert "date" not in s.strong_signals
    assert s.detail["date_skew_days"] == 2.0
    assert s.confidence >= 75


def test_forgiven_date_contributes_no_points():
    # Deliberately below 100 total (site+performer, no title hit) so the
    # min(100, ...) clamp can't mask metadata leaking into the sum.
    dated = score(SCENE, "ThatFetishGirl.2026-07-09.Jane.Doe.Solo.Clip")
    undated = score(SCENE, "ThatFetishGirl.Jane.Doe.Solo.Clip")
    assert dated.veto is None
    assert dated.confidence == undated.confidence < 100


def test_skewed_date_beyond_window_still_vetoes():
    # 4 days off > default window of 3 — hard contradiction even with strong evidence.
    s = score(SCENE, "ThatFetishGirl.2026-07-11.Latex.Worship.Session.Jane.Doe")
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_skewed_date_with_one_strong_signal_vetoes():
    # Site alone can't carry a contradicted date: forgiveness needs two strong signals.
    s = score(SCENE, "ThatFetishGirl.2026-07-09.Something.Unrelated")
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_skew_forgiven_at_exact_window_boundary():
    s = score(SCENE, "ThatFetishGirl.2026-07-10.Latex.Worship.Session.Jane.Doe",
              date_skew_days=3)
    assert s.veto is None
    assert s.detail["date_skew_days"] == 3.0


def test_skew_window_one_restores_hard_veto():
    s = score(SCENE, "ThatFetishGirl.2026-07-09.Latex.Worship.Session.Jane.Doe",
              date_skew_days=1)
    assert s.veto == "date-mismatch"
```

Append to `tests/fixtures/corpus.yaml` (the file's header says every production mismatch gets appended; `*latex`/`&latex` anchors already exist above):

```yaml
- release: "[ScottStark-HouseholdFantasy] Zarina Noir - Big Titty Step Sister Tinder Match (2026-07-05) [1080p]"
  scene:                                                # uploader stamped 07-05 for the 07-07 scene;
    site: "Household Fantasy"                           # forgiven: site + performer stand without the date
    aliases: []
    date: 2026-07-07
    title: "Big Titty Step-Sistinder Match"
    performers: ["Zarina Noir"]
  expect: match
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_matcher.py tests/test_corpus.py -q`
Expected: the six new matcher tests FAIL (forgiven cases get `veto == "date-mismatch"`; `test_skew_window_one_restores_hard_veto` fails with `TypeError: score() got an unexpected keyword argument 'date_skew_days'`), the new corpus entry FAILS with confidence 0, and every pre-existing test PASSES.

- [ ] **Step 3: Implement forgiveness in `score()`**

In `scenehound/matcher.py`, replace the signature and date block (currently):

```python
def score(
    scene: SceneFingerprint,
    title: str,
    other_sites: frozenset[str] = frozenset(),
) -> MatchScore:
    ngrams = _title_ngrams(title)
    detail: dict[str, float] = {}
    strong: list[str] = []

    # --- date ---
    title_dates = extract_dates(title)
    date_hit = any(abs((d - scene.date).days) <= 1 for d in title_dates)
    if date_hit:
        strong.append("date")
        detail["date"] = STRONG_DATE
    elif title_dates:
        return MatchScore(0, (), "date-mismatch", {"date": 0.0})
```

with:

```python
def score(
    scene: SceneFingerprint,
    title: str,
    other_sites: frozenset[str] = frozenset(),
    date_skew_days: int = 3,
) -> MatchScore:
    ngrams = _title_ngrams(title)
    detail: dict[str, float] = {}
    strong: list[str] = []

    # --- date ---
    title_dates = extract_dates(title)
    date_off: int | None = None  # smallest days-off when no title date is within ±1
    if title_dates:
        off = min(abs((d - scene.date).days) for d in title_dates)
        if off <= 1:
            strong.append("date")
            detail["date"] = STRONG_DATE
        else:
            date_off = off
```

and replace the final block (currently):

```python
    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
```

with:

```python
    # --- date veto, decided after the other signals ---
    # A mismatched date is forgiven only when the skew is small (uploaders
    # stamp rip/upload dates a few days off the studio release date) AND the
    # match clears the two-strong-signal rule without the date. Otherwise it
    # stays a hard contradiction: on daily-release sites the date is often
    # the only thing separating sibling scenes.
    if date_off is not None and (date_off > date_skew_days or len(strong) < 2):
        return MatchScore(0, (), "date-mismatch", {"date": 0.0})

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    if date_off is not None:
        detail["date_skew_days"] = float(date_off)  # trace metadata, not points
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
```

(`detail["date_skew_days"]` is added AFTER `sum(detail.values())` so it never contributes points — `test_forgiven_date_contributes_no_points` guards this.)

Add one bullet to the module docstring's list (after the Title bullet, before the closing `"""`):

```
- A parsed date that contradicts the scene's date is a hard veto, EXCEPT when
  the skew is within date_skew_days (default 3) and the match clears the
  two-strong-signal rule without the date — uploaders sometimes stamp
  rip/upload dates a few days off the studio release date. A forgiven date
  contributes no points; the skew is recorded in detail["date_skew_days"]
  for the UI trace.
```

Note one deliberate side effect: when a candidate has BOTH a skewed date and a foreign site, the veto reported is now `site-mismatch` (the site block returns first). Both are hard rejections; the spec records this as not load-bearing.

- [ ] **Step 4: Run the matcher and corpus tests**

Run: `.venv/bin/python -m pytest tests/test_matcher.py tests/test_corpus.py -q`
Expected: ALL PASS (including the pre-existing `test_conflicting_date_vetoes` — 2025-01-01 is far beyond any window — and the corpus entry `ThatFetishGirl.2024-01-01...` which stays `no_match`).

- [ ] **Step 5: Run the full suite to catch collateral damage**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS (the default `date_skew_days=3` changes no call site's behaviour except previously-vetoed strong matches, which no existing test asserts on).

- [ ] **Step 6: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py tests/fixtures/corpus.yaml
git commit -m "feat: forgive small release-date skew when two strong signals stand without it"
```

---

### Task 2: Config — `matching.date_skew_days`

**Files:**
- Modify: `scenehound/config.py` (`MatchingConfig` at line 24–27; `load_config`'s `matching=` block at lines 139–144)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `MatchingConfig.date_skew_days: int = 3`, populated from yaml `matching.date_skew_days` and env `SCENEHOUND_DATE_SKEW_DAYS` (env wins). Task 3 reads `config.matching.date_skew_days`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (uses the existing `write_config` helper and `MINIMAL_YAML`):

```python
def test_matching_date_skew_days_default(tmp_path):
    cfg = load_config(write_config(tmp_path), env={})
    assert cfg.matching.date_skew_days == 3


def test_matching_date_skew_days_yaml_and_env(tmp_path):
    text = MINIMAL_YAML + "\nmatching:\n  date_skew_days: 5\n"
    assert load_config(write_config(tmp_path, text), env={}).matching.date_skew_days == 5
    cfg = load_config(
        write_config(tmp_path, text), env={"SCENEHOUND_DATE_SKEW_DAYS": "1"}
    )
    assert cfg.matching.date_skew_days == 1  # env wins over yaml
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: the two new tests FAIL with `AttributeError: 'MatchingConfig' object has no attribute 'date_skew_days'`; the rest PASS.

- [ ] **Step 3: Add the field and plumbing**

In `scenehound/config.py`, extend the dataclass:

```python
@dataclass(frozen=True)
class MatchingConfig:
    threshold: int = 75
    max_queries_per_search: int = 5
    date_skew_days: int = 3
```

and in `load_config`, extend the `matching=MatchingConfig(...)` construction:

```python
        matching=MatchingConfig(
            threshold=int(env.get("SCENEHOUND_THRESHOLD", m.get("threshold", 75))),
            max_queries_per_search=int(
                env.get("SCENEHOUND_MAX_QUERIES", m.get("max_queries_per_search", 5))
            ),
            date_skew_days=int(
                env.get("SCENEHOUND_DATE_SKEW_DAYS", m.get("date_skew_days", 3))
            ),
        ),
```

- [ ] **Step 4: Run the config tests**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add scenehound/config.py tests/test_config.py
git commit -m "feat: matching.date_skew_days config (SCENEHOUND_DATE_SKEW_DAYS, default 3)"
```

---

### Task 3: Thread the configured window to every `score()` call site

**Files:**
- Modify: `scenehound/api.py` (`_search_mode` around lines 106 & 124; `_rss_mode` around lines 164 & 177)
- Modify: `scenehound/import_completer.py` (`_match_one` at line 198; `match_pack` at line 227; `ImportCompleter.__init__` at line 284; the `match_pack(...)` call at line 427)
- Modify: `scenehound/app.py` (`ImportCompleter(...)` construction at line 110)
- Modify: `tests/conftest.py` (`build_app` at line 63; `make_app` fixture at line 105)
- Test: `tests/test_api.py`, `tests/test_import_completer.py`

**Interfaces:**
- Consumes: `score(..., date_skew_days=...)` from Task 1; `config.matching.date_skew_days` from Task 2.
- Produces: `match_pack(item, candidates, index, config, date_skew_days: int = 3)`; `ImportCompleter.__init__(client, index_holder, config, store=None, date_skew_days: int = 3)`; `build_app(prowlarr_calls, store=None, with_index=True, status=200, config=None, feed=FEED_MATCHING)`; `make_app`'s inner `_make(store=None, with_index=True, status=200, matching=None, feed=FEED_MATCHING)`.

- [ ] **Step 1: Extend the test fixtures**

In `tests/conftest.py`, change `build_app` to accept a config and feed (defaults preserve every existing caller):

```python
def build_app(prowlarr_calls, store=None, with_index=True, status=200,
              config=None, feed=FEED_MATCHING):
    def handler(request: httpx.Request) -> httpx.Response:
        prowlarr_calls.append(dict(request.url.params))
        return httpx.Response(status, content=feed)

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = config or make_config()
```

(the rest of the function body is unchanged) and widen the `make_app` fixture's builder:

```python
@pytest.fixture
def make_app(prowlarr_calls):
    """Builder for tests that need non-default apps (no index / error feeds)."""
    def _make(store=None, with_index=True, status=200, matching=None, feed=FEED_MATCHING):
        config = make_config(matching=matching) if matching is not None else None
        return build_app(prowlarr_calls, store=store, with_index=with_index,
                         status=status, config=config, feed=feed)
    return _make
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from scenehound.config import MatchingConfig

FEED_SKEWED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.26.07.05.Latex.Worship.Session.Jane.Doe.1080p</title>
      <guid>g-skew</guid><link>http://p/dl/9</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def test_search_respects_configured_date_skew(make_app):
    # Release stamped 07-05 for the 07-07 scene (2 days off, site+performer+title).
    params = {"t": "search", "q": "thatfetishgirl 07.07.2026",
              "cat": "6000", "apikey": "shk"}
    # Default window (3): forgiven and rewritten.
    lenient = TestClient(make_app(feed=FEED_SKEWED))
    assert len(titles(lenient.get("/indexer/empornium/api", params=params))) == 1
    # Window 1: the old hard veto — proves the CONFIGURED value reaches score().
    strict = TestClient(make_app(
        matching=MatchingConfig(date_skew_days=1), feed=FEED_SKEWED))
    assert titles(strict.get("/indexer/empornium/api", params=params)) == []
```

Append to `tests/test_import_completer.py` (in the phase-2 section; `_packcand`, `_index`, `SCENE_A`, `BY_ID`, `queue_item_from_record`, `ImportCompleterConfig` already exist there):

```python
def test_match_pack_forgives_small_date_skew():
    # Uploader stamped 07.05 for the 07-07 scene; site+performer+title stand alone.
    cands = [_packcand("TFG.26.07.05.Latex.Worship.Session.Jane.Doe.1080p.mp4")]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A),
                      ImportCompleterConfig(import_threshold=75))
    assert pack.fully_matched
    assert pack.matched_movie_ids == frozenset({101})


def test_match_pack_threads_date_skew_days():
    cands = [_packcand("TFG.26.07.05.Latex.Worship.Session.Jane.Doe.1080p.mp4")]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A),
                      ImportCompleterConfig(import_threshold=75), date_skew_days=1)
    assert not pack.fully_matched
```

- [ ] **Step 3: Run to verify the right failures**

Run: `.venv/bin/python -m pytest tests/test_api.py tests/test_import_completer.py -q`
Expected: `test_search_respects_configured_date_skew` FAILS on the strict half (score() still uses its default 3, so the release matches even with `date_skew_days=1` configured); `test_match_pack_forgives_small_date_skew` PASSES already (Task 1's default); `test_match_pack_threads_date_skew_days` FAILS with `TypeError: match_pack() got an unexpected keyword argument 'date_skew_days'`. Everything else PASSES.

- [ ] **Step 4: Thread the value**

`scenehound/api.py`, `_search_mode` — next to the existing threshold read (line 106):

```python
    threshold = state.config.matching.threshold
    skew = state.config.matching.date_skew_days
```

and the call at line 124:

```python
                        s = score(scene, c.title, other_sites=index.other_sites_for(scene),
                                  date_skew_days=skew)
```

`_rss_mode` — after `index = state.index_holder.current` (line 164):

```python
    skew = state.config.matching.date_skew_days
```

and the call at line 177:

```python
                s = score(scene, c.title, other_sites=index.other_sites_for(scene),
                          date_skew_days=skew)
```

`scenehound/import_completer.py`:

```python
def _match_one(
    cand: ManualImportItem, item: QueueItem, index, config: ImportCompleterConfig,
    date_skew_days: int = 3,
) -> FileMatch:
```

with the score call inside becoming:

```python
        s = score(scene, name, other_sites=index.other_sites_for(scene),
                  date_skew_days=date_skew_days)
```

```python
def match_pack(
    item: QueueItem, candidates: list[ManualImportItem], index,
    config: ImportCompleterConfig, date_skew_days: int = 3,
) -> PackMatch:
    videos = [c for c in candidates if not c.is_sample]
    return PackMatch(tuple(
        _match_one(c, item, index, config, date_skew_days) for c in videos
    ))
```

`ImportCompleter.__init__` gains the window (keyword, after `store`):

```python
    def __init__(self, client, index_holder, config: ImportCompleterConfig,
                 store=None, date_skew_days: int = 3) -> None:
        ...
        self._date_skew_days = date_skew_days
```

and the `_plan_phase2` call at line 427:

```python
        pack = match_pack(item, candidates, index, self._config, self._date_skew_days)
```

`scenehound/app.py`, the construction at line 110:

```python
                completer = ImportCompleter(
                    whisparr, state.index_holder, config.import_completer,
                    store=state.store,
                    date_skew_days=config.matching.date_skew_days,
                )
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add scenehound/api.py scenehound/import_completer.py scenehound/app.py tests/conftest.py tests/test_api.py tests/test_import_completer.py
git commit -m "feat: thread matching.date_skew_days to all score() call sites"
```

---

### Task 4: UI trace — veto copy and forgiven-skew marker

**Files:**
- Modify: `scenehound/static/ui.html` (`VETO_TEXT` at line ~118; `whyLines()` at lines ~121–140)

**Interfaces:**
- Consumes: `detail.date_skew_days` from Task 1 (already delivered to the browser — `CandidateTrace.detail` flows through `observe.py`/`ui_api.py` untouched).
- Produces: nothing consumed later.

There is no JS test harness; the existing Python suite guards the JSON shape and this task changes only presentation strings, so the steps are edit → suite → visual sanity check.

- [ ] **Step 1: Update the hard-veto copy**

In `VETO_TEXT`, replace the `date-mismatch` line:

```js
const VETO_TEXT = {
  "date-mismatch": "Rejected: the release's date is too far from the scene's — or the match is too weak to forgive the gap.",
  "site-mismatch": "Rejected: the title names a different studio from your wanted list.",
};
```

- [ ] **Step 2: Render the forgiven-skew marker**

In `whyLines(c, threshold)`, after the `for (const sig of c.strong_signals)` loop and before the `if (!c.strong_signals.includes("title") ...)` line, insert:

```js
  if (c.detail.date_skew_days != null)
    out.push(`<span class="warn">⚠ Release date is ${c.detail.date_skew_days} day(s) off the scene's — forgiven: ${c.strong_signals.length} other strong signals.</span>`);
```

(The `warn` CSS class already exists — it styles the single-signal-cap line.)

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS (no Python behaviour changed in this task).

- [ ] **Step 4: Visual sanity check**

Run: `.venv/bin/python -c "
from datetime import date
from scenehound.matcher import score
from scenehound.models import SceneFingerprint
s = SceneFingerprint(20, 'Household Fantasy', (), date(2026,7,7),
                     'Big Titty Step-Sistinder Match', ('Zarina Noir',))
ms = score(s, '[ScottStark-HouseholdFantasy] Zarina Noir - Big Titty Step Sister Tinder Match (2026-07-05) [1080p]')
print(ms.confidence, ms.strong_signals, ms.veto, ms.detail)
"`
Expected: confidence ≥ 75, `('site', 'performer')` in strong signals, `veto=None`, and `'date_skew_days': 2.0` in detail — the values the marker renders from.

- [ ] **Step 5: Commit**

```bash
git add scenehound/static/ui.html
git commit -m "feat: UI trace shows forgiven date skew; updated date-mismatch copy"
```
