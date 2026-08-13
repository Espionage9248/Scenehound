# Honest Titles & Superset-Title Veto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the real tracker title in front of the human in Whisparr's Interactive Search, and stop candidates that merely *contain* the scene title from ever being returned.

**Architecture:** Two independent changes to pure functions. Part A appends the verbatim tracker title to the emitted Torznab `<title>` behind a config flag. Part B adds a `superset-title` veto to the matcher: a candidate carrying the whole scene title *plus* identity tokens no signal explains names a longer-titled scene. Both are I/O-free and corpus-testable.

**Tech Stack:** Python 3.12+, FastAPI, pytest, rapidfuzz, PyYAML. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-09-honest-titles-superset-veto-design.md`

## Global Constraints

- Matcher and rewriter stay **pure functions with no I/O**. Accuracy must remain testable against `tests/fixtures/corpus.yaml`.
- **The corpus is the ratchet.** Every production mismatch becomes a corpus row. No task may make an existing `expect:` row change verdict.
- **Automated running leans strict.** A false import is expensive to unpick; a false negative leaves a scene missing and RSS may catch it later.
- **No edit-distance fallback for name matching.** Ruled out at `scenehound/matcher.py:143-155` — it fabricates strong signals from coincidental near-spellings.
- Any failure still degrades to passthrough, never to a broken search.
- Run tests with `.venv/bin/pytest` from the repo root.
- Branch is already created: `feat/honest-titles-superset-veto`. Commit after every task.

---

### Task 1: Date spans

`extract_dates` already finds dates with three regexes but discards *where* they were. Part B needs the character spans so a digit already scored as the date signal is not also counted as a foreign title word — scoring one fact twice is the exact error the de-named foreign-title ratio already corrects for names.

**Files:**
- Modify: `scenehound/dates.py` (append after `extract_dates`, ends line 104)
- Test: `tests/test_dates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `date_spans(text: str) -> list[tuple[int, int]]` — character `(start, end)` half-open spans, one per regex match, possibly overlapping, unsorted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dates.py`:

```python
from scenehound.dates import date_spans


def test_date_spans_locates_each_supported_format():
    for text, want in [
        ("ThatFetishGirl.26.07.07.Latex.Worship.XXX.1080p", "26.07.07"),
        ("Scott Stark Studios - Beach Day 05.07.2026 1080p", "05.07.2026"),
        ("Studio 2026-07-07 Some Scene", "2026-07-07"),
    ]:
        spans = date_spans(text)
        assert any(text[a:b] == want for a, b in spans), f"{want!r} not found in {spans}"


def test_date_spans_empty_when_no_date_present():
    assert date_spans("Xev Bellringer - Mommy Swallows 2") == []
    assert date_spans("Case No. 2658794") == []


def test_date_spans_does_not_swallow_a_long_identity_number():
    text = "ShopLyfter - 18.07.25 - Case No. 2658794 - 1080p"
    covered = {text[a:b] for a, b in date_spans(text)}
    assert "18.07.25" in covered
    assert not any("2658794" in c for c in covered)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dates.py -k date_spans -v`
Expected: FAIL — `ImportError: cannot import name 'date_spans'`

- [ ] **Step 3: Implement `date_spans`**

Append to `scenehound/dates.py`:

```python
def date_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every date-shaped run extract_dates considers.

    Spans may overlap and are not sorted; callers only ask whether a token
    falls inside one. The matcher uses this to tell a digit already explained
    by the date signal from one that is part of the candidate's own title:
    counting "26.07.07" as foreign title words scores one fact twice."""
    return [m.span() for rx in (_YMD4, _XY4, _TRIPLE2) for m in rx.finditer(text)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dates.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add scenehound/dates.py tests/test_dates.py
git commit -m "feat: expose date_spans for residual attribution"
```

---

### Task 2: Name n-grams

The residual calculation must recognise a performer whose punctuation the release renders differently. `Jane O'Neil` tokenizes to `[jane, o, neil]`, so a release spelling it `Jane.ONeil` leaves `oneil` looking like a foreign word. Squashed contiguous runs of the name's *own* tokens cover it without any fuzzy matching. This mirrors `matcher._title_ngrams` applied to the name side instead of the title side.

**Files:**
- Modify: `scenehound/normalize.py` (append after `identity_tokens`, ends line 77)
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `name_ngrams(names: Iterable[str]) -> frozenset[str]` — every squashed contiguous run of tokens from every supplied name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_normalize.py`:

```python
from scenehound.normalize import name_ngrams


def test_name_ngrams_covers_punctuation_glued_by_the_release():
    grams = name_ngrams(["Jane O'Neil"])
    assert {"jane", "o", "neil", "janeo", "oneil", "janeoneil"} <= grams


def test_name_ngrams_covers_hyphenated_names():
    grams = name_ngrams(["Mary-Jane Smith"])
    assert {"maryjane", "maryjanesmith", "jane", "smith"} <= grams


def test_name_ngrams_merges_multiple_names():
    grams = name_ngrams(["Xev Bellringer", "XevBellringer"])
    assert "xevbellringer" in grams
    assert "xev" in grams and "bellringer" in grams


def test_name_ngrams_of_nothing_is_empty():
    assert name_ngrams([]) == frozenset()
    assert name_ngrams([""]) == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_normalize.py -k name_ngrams -v`
Expected: FAIL — `ImportError: cannot import name 'name_ngrams'`

- [ ] **Step 3: Implement `name_ngrams`**

Add `from typing import Iterable` to the imports at the top of `scenehound/normalize.py`, then append:

```python
def name_ngrams(names: Iterable[str]) -> frozenset[str]:
    """Every squashed contiguous run of each name's own tokens.

    The mirror of matcher._title_ngrams, applied to the name side. "Jane
    O'Neil" yields {jane, o, neil, janeo, oneil, janeoneil}, so a release
    spelling it "Jane.ONeil" leaves no token the scene cannot explain. No
    edit-distance fallback: matching names by edit distance fabricates strong
    signals from coincidental near-spellings (see matcher._site_in_title)."""
    out: set[str] = set()
    for name in names:
        toks = tokenize(name)
        for i in range(len(toks)):
            acc = ""
            for j in range(i, len(toks)):
                acc += toks[j]
                out.add(acc)
    return frozenset(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_normalize.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add scenehound/normalize.py tests/test_normalize.py
git commit -m "feat: name_ngrams for punctuation-tolerant name attribution"
```

---

### Task 3: The superset-title veto

The defect: `near_exact` at `scenehound/matcher.py:226-235` asks only whether all the scene's identity tokens are *present* in the candidate. It never asks what else the candidate brought. `Mommy Swallows Before School` contains `Mommy Swallows`, earns `STRONG_TITLE`, and the resulting `"title" in strong` then **exempts it from the foreign-title veto** at line 272.

The existing sibling arm cannot catch this shape — it fires when a candidate *drops* scene words; these keep every word and add more, so `coverage` is 1.0. Demotion was rejected on evidence: `35 + 35 + 25 = 95`, still far above the threshold of 75. It has to be a veto.

**Files:**
- Modify: `tests/fixtures/corpus.yaml` (append)
- Modify: `scenehound/matcher.py` (imports; new helper; new veto arm after the date veto at line 250-256; module docstring)
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `dates.date_spans` (Task 1), `normalize.name_ngrams` (Task 2).
- Produces: `MatchScore(veto="superset-title")`. `MatchScore` gains a fifth field `residual: tuple[str, ...] = ()`, defaulted so every existing four-argument construction stays valid. Task 4 consumes that field.

- [ ] **Step 1: Write the failing corpus rows**

Append to `tests/fixtures/corpus.yaml`:

```yaml
# 2026-08-09 Xev Bellringer false grab. The wanted scene "Mommy Swallows" is a
# substring of seven longer-titled scenes from the same studio. Containment alone
# made `title` a STRONG signal, which in turn exempted every one of them from the
# foreign-title veto — all scored 100 and were returned to Whisparr. A candidate
# carrying the whole scene title PLUS identity tokens no signal explains names a
# longer-titled scene, not this one.
- release: "Xev Bellringer - Mommy Swallows (.mp4)"     # the one real match
  scene: &mommy
    site: "Xev Bellringer"
    aliases: ["XevBellringer"]
    date: 2015-01-06
    title: "Mommy Swallows"
    performers: ["Xev Bellringer"]
  expect: match

- release: "[XevUnleashed] Xev Bellringer - Mommy Swallows - 4K 2160p"  # bracketed uploader tag
  scene: *mommy
  expect: match

- release: "Xev Bellringer - Mommy Swallows [Bonus Scene] 1080p"        # bracketed filler
  scene: *mommy
  expect: match

- release: "[XevUnleashed] Xev Bellringer - Pregnant Mommy Swallows - 4K 2160p"
  scene: *mommy
  expect: no_match

- release: "[Clips4Sale] Pregnant Mommy Swallows [Xev Bellringer] (720p)"
  scene: *mommy
  expect: no_match

- release: "Xev Bellringer - Pregnant Mommy Swallows (1080p)"
  scene: *mommy
  expect: no_match

- release: "Xev Bellringer - Mommy Swallows 2"          # a bare number IS the identity
  scene: *mommy
  expect: no_match

- release: "Stripper Step-Mommy Swallows Xev Bellringer"
  scene: *mommy
  expect: no_match

- release: "Xev Bellringer - Mommy Swallows Before Your Date (720p)"
  scene: *mommy
  expect: no_match

- release: "Xev Bellringer - Mommy Swallows Before School (.mp4)"
  scene: *mommy
  expect: no_match
```

- [ ] **Step 2: Run the corpus to verify the new rows fail**

Run: `.venv/bin/pytest tests/test_corpus.py -v`
Expected: 7 FAIL, each `expected no_match, got 100 (strong=('site', 'performer', 'title'), ...)`. The 3 `match` rows already pass. Every pre-existing row still passes — if any pre-existing row fails, stop and investigate before writing any implementation.

- [ ] **Step 3: Write the failing unit tests**

Append to `tests/test_matcher.py`:

```python
from scenehound.matcher import _unexplained_residual

MOMMY = SceneFingerprint(
    scene_id=4622,
    site="Xev Bellringer",
    site_aliases=("XevBellringer",),
    date=date(2015, 1, 6),
    title="Mommy Swallows",
    performers=("Xev Bellringer",),
)


def test_superset_title_vetoes_a_longer_titled_scene():
    s = score(MOMMY, "Xev Bellringer - Mommy Swallows Before School (.mp4)")
    assert s.veto == "superset-title"
    assert s.confidence == 0
    assert s.residual == ("before", "school")


def test_superset_title_keeps_the_exact_scene():
    s = score(MOMMY, "Xev Bellringer - Mommy Swallows (.mp4)")
    assert s.veto is None
    assert s.confidence >= 75
    assert s.residual == ()


def test_superset_residual_counts_a_bare_number():
    # "Mommy Swallows 2" is a different scene; identity_tokens keeps the digit.
    assert _unexplained_residual(MOMMY, "Xev Bellringer - Mommy Swallows 2") == ("2",)


def test_superset_residual_explains_junk_and_scene_title():
    assert _unexplained_residual(MOMMY, "Xev Bellringer - Mommy Swallows 1080p x264") == ()


def test_superset_residual_explains_a_bracketed_uploader_tag():
    assert _unexplained_residual(
        MOMMY, "[XevUnleashed] Xev Bellringer - Mommy Swallows - 4K 2160p") == ()
    assert _unexplained_residual(
        MOMMY, "Xev Bellringer - Mommy Swallows {Se7enSeas}") == ()


def test_superset_residual_explains_date_digits():
    scene = SceneFingerprint(1, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane Doe",))
    assert _unexplained_residual(
        scene, "ThatFetishGirl.26.07.07.Latex.Worship.Session.XXX.1080p.MP4-GRP") == ()


def test_superset_residual_explains_a_punctuation_variant_of_a_performer():
    scene = SceneFingerprint(1, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane O'Neil",))
    assert _unexplained_residual(scene, "Jane.ONeil.Latex.Worship.Session.720p") == ()


def test_superset_veto_does_not_fire_without_a_title_signal():
    # Title is not strong here (no site/performer), so the arm must not engage.
    scene = SceneFingerprint(1, "Some Studio", (), date(2026, 7, 7),
                             "Mommy Swallows", ())
    s = score(scene, "Darlingjosefin - Mommy Swallows Sperm (720p)")
    assert s.veto != "superset-title"


def test_date_mismatch_keeps_precedence_over_superset_title():
    s = score(MOMMY, "Xev Bellringer - Mommy Swallows Before School 2020-03-04")
    assert s.veto == "date-mismatch"
```

- [ ] **Step 4: Run the unit tests to verify they fail**

Run: `.venv/bin/pytest tests/test_matcher.py -k superset -v`
Expected: FAIL — `ImportError: cannot import name '_unexplained_residual'`

- [ ] **Step 5: Add the `residual` field to `MatchScore`**

In `scenehound/matcher.py`, replace the `MatchScore` dataclass (lines 121-126):

```python
@dataclass(frozen=True)
class MatchScore:
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]
    # The candidate's identity tokens that no signal explains. Populated only by
    # the superset-title veto; it is the evidence a future veto-override needs to
    # name what it is overriding ("vetoed on: pregnant"). Defaulted so existing
    # four-argument constructions stay valid.
    residual: tuple[str, ...] = ()
```

- [ ] **Step 6: Implement `_unexplained_residual`**

In `scenehound/matcher.py`, add `import re` to the imports and extend the existing import lines:

```python
import re

from scenehound.dates import date_spans, extract_dates
from scenehound.normalize import (
    JUNK_TOKENS, content_tokens, identity_tokens, name_ngrams, squash, tokenize,
)
```

Add these module constants beside the others (after `_MAX_SITE_TOKENS`, line 116-118):

```python
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_TAGGED_SEGMENT_RE = re.compile(r"[\[\{][^\]\}]*[\]\}]")
```

Add the helper after `_performer_present` (which ends line 171):

```python
def _unexplained_residual(scene: SceneFingerprint, title: str) -> tuple[str, ...]:
    """The candidate's identity tokens that no signal accounts for.

    A token is explained when it is junk, part of the scene title, part of a
    site/alias/performer name (in any squashed run — "Jane.ONeil"), inside a
    span extract_dates matched (already scored as the date signal), inside a
    [bracketed] or {braced} segment (uploader/studio tags, never title words),
    or positioned after the last junk token (a trailing -GROUP).

    Anything left is the candidate's OWN title vocabulary. Note the two
    deliberate leniencies: bracketed segments and post-junk trailers are
    excluded wholesale, which can only make the veto more forgiving, never
    less. That is the safe direction for a rule whose bar is 1."""
    explained = set(name_ngrams((scene.site, *scene.site_aliases, *scene.performers)))
    explained |= set(identity_tokens(scene.title))
    dates = date_spans(title)
    tags = [m.span() for m in _TAGGED_SEGMENT_RE.finditer(title)]
    toks = [(m.group().lower(), m.span()) for m in _TOKEN_RE.finditer(title)]
    last_junk = max((i for i, (t, _) in enumerate(toks) if t in JUNK_TOKENS), default=-1)
    out: list[str] = []
    for i, (tok, (a, b)) in enumerate(toks):
        if tok in JUNK_TOKENS or tok in explained:
            continue
        if any(s <= a and b <= e for s, e in dates):
            continue
        if any(s <= a and b <= e for s, e in tags):
            continue
        if last_junk >= 0 and i > last_junk:
            continue
        out.append(tok)
    return tuple(out)
```

- [ ] **Step 7: Add the veto arm**

In `score()`, insert immediately **after** the date-veto `return` block (which ends line 256) and **before** the `# --- foreign-title veto ---` comment:

```python
    # --- superset-title veto ---
    # `title` became strong by CONTAINMENT: every scene token is present in the
    # candidate. That says nothing about what else the candidate brought. A
    # candidate carrying the whole scene title plus vocabulary of its own names a
    # longer-titled scene of the same studio ("Mommy Swallows" vs "Mommy Swallows
    # Before School": same site, same performer, both score 100). The foreign-title
    # veto below cannot catch this — it needs `title` absent from the strong set,
    # and its sibling arm measures words the candidate DROPPED, which here is none
    # (coverage 1.0). Demotion does not work either: 35 + 35 + 25 = 95, still over
    # threshold. Placed after the date veto so a contradicting date keeps
    # precedence and existing corpus rows keep their veto strings.
    if "title" in strong and (residual := _unexplained_residual(scene, title)):
        detail["superset_residual"] = float(len(residual))
        return MatchScore(0, tuple(strong), "superset-title", detail, residual)
```

- [ ] **Step 8: Add the module docstring entry**

In the module docstring of `scenehound/matcher.py`, insert after the sibling-scene bullet (which ends line 79, before the closing `"""`):

```
- Superset-title veto: `title` goes strong on CONTAINMENT — every scene token
  present in the candidate — which says nothing about what the candidate brought
  of its own. "Mommy Swallows" is a substring of "Mommy Swallows Before School",
  "Pregnant Mommy Swallows" and "Stripper Step-Mommy Swallows"; all three are
  different scenes of the same studio, all scored 100, and the strong `title`
  additionally EXEMPTED them from the foreign-title veto (2026-08-09 Xev
  Bellringer false grab: 9 of 10 returned releases were the wrong scene). Any
  identity token the candidate carries that no signal explains vetoes the match.
  Explained = junk, scene title, a squashed run of a site/alias/performer name,
  inside a parsed date span (already scored as the date signal — counting it as
  title vocabulary scores one fact twice), inside a [bracketed] or {braced}
  segment, or after the last junk token (a trailing -GROUP). The bar is 1: on
  the corpus every genuine match leaves zero residual, so any residual at all is
  evidence of a different scene. The residual tokens are returned on
  MatchScore.residual as the evidence a veto-override would name.
```

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. Specifically: all 40 corpus rows green, all `superset` unit tests green, and **no pre-existing test changed verdict**. Two suites matter most here. If `tests/test_api.py` or `tests/test_observe.py` fail, the veto changed a search-path result it should not have. If `tests/test_import_corpus.py` fails, it changed the import-completer's multipack file matching — the matcher is shared, so the veto reaches there too. That reach is intended (it strictly reduces auto-imports, the safe direction), but a *changed* import-corpus verdict means a pack file that used to match no longer does, and that needs a look before proceeding.

- [ ] **Step 10: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py tests/fixtures/corpus.yaml
git commit -m "fix: veto candidates that merely contain the scene title

A candidate carrying the whole scene title plus identity tokens no signal
explains names a longer-titled scene of the same studio. Containment alone
made title a STRONG signal, which also exempted these from the foreign-title
veto — 9 of 10 releases returned for one Xev Bellringer search were the wrong
scene, every one scoring 100."
```

---

### Task 4: Surface the veto in the UI

The veto is only useful if an over-fire is visible. Vetoed candidates are never returned to Whisparr, so `/ui` is the only place a lost match can be seen. `VETO_TEXT` already falls back to `"Rejected: " + c.veto`, so this task upgrades readable-but-terse into a named cause.

**Files:**
- Modify: `scenehound/observe.py` (`CandidateTrace`, ends line 80; `scored()`, ends line 331)
- Modify: `scenehound/static/ui.html` (`VETO_TEXT`, lines 118-122; `whyLines`, line 126)
- Test: `tests/test_observe.py`

**Interfaces:**
- Consumes: `MatchScore.residual` (Task 3).
- Produces: `CandidateTrace.residual: tuple[str, ...]`, serialized to JSON as a list by the existing `dataclasses.asdict` path in `SessionStore.snapshot`. No `ui_api.py` change is needed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_observe.py`:

`MatchScore`, `ReleaseCandidate` and `SCENE` are all already available at module scope in `tests/test_observe.py` (lines 5, 6 and 77) — no new imports.

```python
def test_snapshot_carries_the_superset_residual(store):
    rec = store.recorder("empornium", 75, "xevbellringer 06.01.2015")
    cand = ReleaseCandidate(title="Xev Bellringer - Mommy Swallows Before School",
                            guid="g1", link="http://p/dl/1")
    ms = MatchScore(0, ("site", "performer", "title"), "superset-title",
                    {"superset_residual": 2.0}, ("before", "school"))
    rec.scored([(cand, SCENE, ms, None)])
    rec.commit()

    c = store.snapshot()["sessions"][0]["candidates"][0]
    assert c["veto"] == "superset-title"
    assert c["residual"] == ["before", "school"]
```

The assertion is `["before", "school"]`, not a tuple: `SessionStore.snapshot` converts tuples to lists for JSON via `_to_json_safe`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_observe.py -k superset_residual -v`
Expected: FAIL — `KeyError: 'residual'`

- [ ] **Step 3: Add the field to `CandidateTrace`**

In `scenehound/observe.py`, add to `CandidateTrace` after `rewritten_title` (line 80):

```python
    # Identity tokens the matcher could not explain; populated for the
    # superset-title veto only. The UI names them so an over-firing veto is
    # visible — vetoed candidates never reach Whisparr, so /ui is the only
    # place a wrongly-rejected release can be seen.
    residual: tuple[str, ...] = ()
```

- [ ] **Step 4: Populate it in `scored()`**

In `scenehound/observe.py`, add to the `CandidateTrace(...)` construction inside `scored()`, after `rewritten_title=rewritten,`:

```python
                residual=ms.residual,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_observe.py -v`
Expected: PASS (all)

- [ ] **Step 6: Add the plain-language veto text**

In `scenehound/static/ui.html`, add to `VETO_TEXT` after the `foreign-title` entry (line 121):

```javascript
  "superset-title": "Rejected: the release contains the whole scene title but adds words of its own — it names a longer-titled scene from the same studio.",
```

Then in `whyLines`, replace the veto branch (lines 125-128) so the residual is named:

```javascript
  if (c.veto) {
    out.push(`<span class="no">✗ ${esc(VETO_TEXT[c.veto] || "Rejected: " + c.veto)}</span>`);
    if (c.residual && c.residual.length)
      out.push(`<span class="warn">Unexplained words: ${esc(c.residual.join(", "))}</span>`);
    return out;
  }
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add scenehound/observe.py scenehound/static/ui.html tests/test_observe.py
git commit -m "feat: name the superset veto's unexplained words in the UI"
```

---

### Task 5: Rewriter carries the original title

Pure-function half of Part A. `include_original` is keyword-only and defaults to **False** so this task changes no behaviour anywhere — the config default that turns it on lands in Task 6. That split keeps this task independently reviewable and keeps the pure function's historical contract intact for any caller that does not opt in.

**Files:**
- Modify: `scenehound/rewriter.py:47-55`
- Test: `tests/test_rewriter.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `rewrite_title(scene: SceneFingerprint, original_title: str, *, include_original: bool = False) -> str`. Task 6 calls it with an explicit keyword argument at all three `api.py` call sites.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rewriter.py`:

```python
def test_rewrite_appends_the_original_verbatim():
    out = rewrite_title(SCENE, "messy jane doe 07/07/26 [1080] x264", include_original=True)
    assert out == (
        "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX.1080p.x264"
        " [messy jane doe 07/07/26 [1080] x264]"
    )


def test_rewrite_original_suffix_is_opt_in():
    plain = rewrite_title(SCENE, "messy jane doe [1080]")
    assert plain == "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX.1080p"
    assert "[" not in plain.split("XXX")[1]


def test_rewrite_appends_original_even_without_quality_tokens():
    out = rewrite_title(SCENE, "Jane Doe - Some Great Scene", include_original=True)
    assert out == (
        "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX"
        " [Jane Doe - Some Great Scene]"
    )


def test_rewrite_does_not_escape_brackets_in_the_original():
    # Nesting is cosmetic; Whisparr's parser ignores everything after the
    # quality token, so the payload is passed through untouched.
    out = rewrite_title(SCENE, "[Studio] Scene [1080]", include_original=True)
    assert out.endswith(" [[Studio] Scene [1080]]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rewriter.py -k original -v`
Expected: FAIL — `TypeError: rewrite_title() got an unexpected keyword argument 'include_original'`

- [ ] **Step 3: Implement the suffix**

Replace `rewrite_title` in `scenehound/rewriter.py`:

```python
def rewrite_title(
    scene: SceneFingerprint, original_title: str, *, include_original: bool = False
) -> str:
    """The canonical name Whisparr parses, optionally followed by the tracker's
    own title in square brackets.

    The suffix is verbatim — not scrubbed, not truncated. Verified against
    Whisparr's own GET /api/v3/parse (2026-08-09): studio, releaseDate, quality
    and scene mapping are identical with and without it, because Whisparr
    identifies an adult scene by studio + date and ignores everything after the
    quality token. A DASH separator is deliberately not used: it made the parser
    emit a phantom releaseGroup, which would reach Whisparr's file naming.

    Defaults to off so the pure function keeps its historical contract; the
    service passes the config value explicitly at every call site."""
    parts = [
        _dotify(scene.site),
        scene.date.isoformat(),
        _dotify(scene.title),
        "XXX",
        *extract_quality_tokens(original_title),
    ]
    canonical = ".".join(p for p in parts if p)
    return f"{canonical} [{original_title}]" if include_original else canonical
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rewriter.py -v`
Expected: PASS (all, including the pre-existing `test_rewrite_full` and `test_rewrite_without_quality`)

- [ ] **Step 5: Run the full suite to confirm nothing else moved**

Run: `.venv/bin/pytest -q`
Expected: PASS — the default is off, so no call site behaves differently yet.

- [ ] **Step 6: Commit**

```bash
git add scenehound/rewriter.py tests/test_rewriter.py
git commit -m "feat: rewrite_title can carry the original tracker title"
```

---

### Task 6: Wire the suffix on

Turns Part A on by default and threads one flag value to all three call sites.

**The hazard this task exists to manage:** `rewrite_title` is called three times in `api.py` — line 148 (recorder), 156 (feed), 187 (RSS). The recorder's stored `rewritten_title` is what `observe.py:195` matches incoming grab webhooks against. **If lines 148 and 156 disagree, grab correlation silently stops working** and the UI's Grabbed/Imported ladder stalls at Matched with no error anywhere. Step 4's assertion is the guard.

**Files:**
- Modify: `scenehound/config.py` (new `NamingConfig`, new `_naming()` loader, `Config.naming` field)
- Modify: `scenehound/api.py:148`, `:156`, `:187`
- Modify: `tests/conftest.py` (`make_config` base, `make_app` fixture)
- Modify: `tests/test_api.py:33`, `:100`, `:162`
- Modify: `README.md`
- Test: `tests/test_api.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `rewrite_title(..., include_original=...)` (Task 5).
- Produces: `Config.naming.original_title_suffix: bool`, default `True`, env override `SCENEHOUND_ORIGINAL_TITLE_SUFFIX`.

- [ ] **Step 1: Update the three existing title assertions**

The feed item in `tests/conftest.py` is titled `TFG.26.07.07.Latex.Worship.Session.1080p`, so with the suffix on the emitted title becomes the canonical name plus that string in brackets. Define the expected value once at the top of `tests/test_api.py`, after the imports:

```python
REWRITTEN = (
    "That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p"
    " [TFG.26.07.07.Latex.Worship.Session.1080p]"
)
```

Then replace the three literals:

- line 33: `assert got == [REWRITTEN]`
- line 100: `assert REWRITTEN in got`
- line 162: `assert titles(r) == [REWRITTEN]`

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_feed_title_matches_the_recorded_title(app_with_store, store):
    # The recorder's rewritten_title is what observe.record_grab correlates
    # incoming grab webhooks against. If the feed and the recorder disagree,
    # grab tracking silently stops working.
    from fastapi.testclient import TestClient

    r = TestClient(app_with_store).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    feed_title = titles(r)[0]
    recorded = [c for c in store.snapshot()["sessions"][0]["candidates"] if c["matched"]]
    assert recorded[0]["rewritten_title"] == feed_title


def test_original_title_attr_survives_the_suffix(client):
    from scenehound.torznab import ORIGINAL_TITLE_ATTR

    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    root = ET.fromstring(r.content)
    attrs = root.findall(
        ".//channel/item/{http://torznab.com/schemas/2015/feed}attr")
    values = {a.get("name"): a.get("value") for a in attrs}
    assert values[ORIGINAL_TITLE_ATTR] == "TFG.26.07.07.Latex.Worship.Session.1080p"


def test_suffix_can_be_switched_off(make_app):
    from fastapi.testclient import TestClient

    from scenehound.config import NamingConfig

    app = make_app(naming=NamingConfig(original_title_suffix=False))
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    assert titles(r) == ["That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p"]
```

Append to `tests/test_config.py`:

```python
def test_naming_defaults_to_suffix_on(tmp_path):
    (tmp_path / "config.yaml").write_text("indexers: []\n")
    cfg = load_config(tmp_path, {"SCENEHOUND_API_KEY": "k"})
    assert cfg.naming.original_title_suffix is True


def test_naming_reads_yaml_and_env(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "indexers: []\nnaming:\n  original_title_suffix: false\n")
    cfg = load_config(tmp_path, {"SCENEHOUND_API_KEY": "k"})
    assert cfg.naming.original_title_suffix is False

    cfg = load_config(tmp_path, {"SCENEHOUND_API_KEY": "k",
                                 "SCENEHOUND_ORIGINAL_TITLE_SUFFIX": "true"})
    assert cfg.naming.original_title_suffix is True
```

`load_config` is already imported at the top of `tests/test_config.py`, and these tests read `cfg.naming` off the returned object, so no new import is needed there.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'naming'`, plus the three updated literals failing because the suffix is not wired yet.

- [ ] **Step 4: Add `NamingConfig`**

In `scenehound/config.py`, add after `UiConfig` (ends line 52):

```python
@dataclass(frozen=True)
class NamingConfig:
    # Append the tracker's own title, verbatim, to the emitted Torznab <title>.
    # An output concern, deliberately not under `matching:` — that section is
    # the accuracy knobs. Default on: Whisparr's parser ignores everything after
    # the quality token (verified 2026-08-09), so this costs nothing, and an
    # escape hatch defaulted off is an escape hatch nobody finds.
    original_title_suffix: bool = True
```

Add to `Config` after `ui`:

```python
    naming: NamingConfig = field(default_factory=NamingConfig)
```

Add the loader beside `_ui` (ends line 124):

```python
def _naming(raw: dict, env: Mapping[str, str]) -> NamingConfig:
    d = NamingConfig()
    n = raw.get("naming", {}) or {}
    return NamingConfig(
        original_title_suffix=_env_bool(
            env, "SCENEHOUND_ORIGINAL_TITLE_SUFFIX",
            bool(n.get("original_title_suffix", d.original_title_suffix)),
        ),
    )
```

And wire it into `load_config`'s `Config(...)` construction, after `ui=_ui(raw, env),`:

```python
        naming=_naming(raw, env),
```

- [ ] **Step 5: Thread the flag through `api.py`**

Read the flag **once per request** and pass the same value to every call site. In `_search_mode`, add it beside the other request-scoped locals at line 106-108:

```python
    threshold = state.config.matching.threshold
    skew = state.config.matching.date_skew_days
    suffix = state.config.naming.original_title_suffix
    bucket = state.buckets[indexer.slug]
```

Replace the `rec.scored([...])` block (lines 146-150):

```python
    rec.scored([
        (v.candidate, v.scene, v.score,
         rewrite_title(v.scene, v.candidate.title, include_original=suffix)
         if v.confidence >= threshold else None)
        for v in best.values()
    ])
```

Replace the `return` block (lines 155-158):

```python
    return _xml(build_feed([
        FeedEntry(v.candidate,
                  title_override=rewrite_title(v.scene, v.candidate.title,
                                               include_original=suffix))
        for v in matched
    ]))
```

In `_rss_mode`, add beside the other locals (line 166-168):

```python
    skew = state.config.matching.date_skew_days
    suffix = state.config.naming.original_title_suffix
    entries: list[FeedEntry] = []
```

and replace line 187:

```python
                new_title = rewrite_title(best_scene, c.title, include_original=suffix)
```

- [ ] **Step 6: Let tests build a config with a `naming` override**

In `tests/conftest.py`, add `NamingConfig` to the `scenehound.config` import list, add to the `make_config` base dict:

```python
        naming=NamingConfig(),
```

and extend the `make_app` fixture's inner `_make` so it can pass one through:

```python
    def _make(store=None, with_index=True, status=200, matching=None,
              naming=None, feed=FEED_MATCHING):
        overrides = {}
        if matching is not None:
            overrides["matching"] = matching
        if naming is not None:
            overrides["naming"] = naming
        config = make_config(**overrides) if overrides else None
        return build_app(prowlarr_calls, store=store, with_index=with_index,
                         status=status, config=config, feed=feed)
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. If `test_feed_title_matches_the_recorded_title` fails, one of the three call sites was missed — that is exactly the failure this task exists to prevent.

- [ ] **Step 8: Update the README**

In `README.md`, in the **Logs** section, replace the sentence beginning *"Wrong grab? The original tracker title is in the log line…"* with:

```markdown
Wrong grab? The original tracker title is right there in the result Whisparr
shows you — Scenehound appends it verbatim in square brackets:

    Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.720p [Xev Bellringer - Mommy Swallows Before Your Date (720p)]

Whisparr identifies an adult scene by studio + date and ignores everything after
the quality token, so the suffix costs nothing and makes an Interactive Search
decidable when several releases collapse to one canonical name. Set
`SCENEHOUND_ORIGINAL_TITLE_SUFFIX=false` (or `naming: {original_title_suffix:
false}`) to switch it off. It is also still in the log line and in the
`scenehound_original_title` attribute of every rewritten result — add the case to
`tests/fixtures/corpus.yaml` and it becomes a regression test.
```

In the **Web UI** section, append to the Candidates bullet:

```markdown
  A release that contains the whole scene title but adds words of its own is
  rejected as a longer-titled scene of the same studio, and the UI names the
  words it could not explain.
```

- [ ] **Step 9: Commit**

```bash
git add scenehound/config.py scenehound/api.py tests/conftest.py \
        tests/test_api.py tests/test_config.py README.md
git commit -m "feat: return the tracker's own title alongside the canonical name

Whisparr's release grid renders <title> and nothing else, so several releases
of one scene arrive indistinguishable. Verified against GET /api/v3/parse that
studio, date, quality and scene mapping are unchanged by the suffix."
```

---

## Verification

After Task 6, confirm the whole thing end to end:

- [ ] `.venv/bin/pytest -q` — full suite green.
- [ ] `.venv/bin/pytest tests/test_corpus.py -q` — 40 rows, all green.
- [ ] Rebuild the image, run one Interactive Search on the Xev Bellringer scene, and confirm: one result rather than ten, its title carrying the tracker name in brackets.
- [ ] Open `/ui` for that search and confirm the seven rejected rows read *"contains the whole scene title but adds words of its own"* and name their unexplained words.

## Out of scope

- Overriding a veto from the Scenehound UI. `MatchScore.residual` and `CandidateTrace.residual` exist to make it possible; nothing is built.
- Stripping resolution tokens from the suffix payload. Held as the documented fallback if a conflicting-resolution misparse is ever observed in the wild.
- Version bump and release. The repo bumps `pyproject.toml` and the README at release time, which is a separate act from this branch.
