# Foreign-Title Veto Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `foreign-title` veto so it arms for *any* title-less strong set (not just `{site, date}`), killing the 2026-07-15 GloryholeSecrets `{date, performer}` false grab that scores 75 on `main`.

**Architecture:** `scenehound/matcher.py` only. The veto's arming guard changes from the enumerated `set(strong) == {"date", "site"}` to the general `len(strong) >= 2 and "title" not in strong` (covering `{site,date}`, `{date,performer}`, `{site,performer}`, `{site,date,performer}`), and `_FOREIGN_TITLE_RATIO` rises 35 → 40 (the incident's generic scene title fuzz-aligns to 36.4, above the old gate). The residual/known-token machinery is untouched. No `dates.py` change — reading ranks already shipped in PR #20 and the incident's date is a genuine primary-reading match.

**Tech Stack:** Python 3.12+ (CI matrix 3.12 & 3.14), pytest, rapidfuzz. Venv at `.venv/` — run tests as `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-foreign-title-veto-generalization-design.md`

## Global Constraints

- `scenehound/matcher.py` and `scenehound/dates.py` are pure functions — no I/O, no config reads; tunables are module-level constants (no new config entries).
- `tests/fixtures/corpus.yaml` is the accuracy ratchet: every existing entry must keep passing; the GloryholeSecrets incident is appended as `expect: no_match`.
- The wanted-index pre-filter must remain a lossless superset of anything the matcher can accept (existing `test_lossless_*` tests). This change touches scoring only, never retrieval.
- `_MIN_FOREIGN_RESIDUAL = 3` and the **distinct** residual count (`set(cand_ctoks)`) are the PR #20 final-review fixes — do NOT tidy them.
- Commit messages use conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`) and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Run the FULL suite (`.venv/bin/python -m pytest`) before every commit, not just the file you touched.
- Pinned `rapidfuzz.fuzz.token_set_ratio` values these tasks rely on (verified against the real library in `.venv` — do NOT re-derive or estimate):
  - `"case no bending the right way"` vs the GloryholeSecrets candidate content tokens = **36.4** (residual = 5 distinct: `first, glory, gloryholesecrets, hole, s`).
  - `"latex worship session"` vs `"Jane Doe & Mary Major - Latex Worship (07.07.2026) 2160p"` = **76.5** (residual 0).
  - `"latex worship session"` vs `"ThatFetishGirl Jane Doe - Wildly Different Bondage Clip (2026-07-07) 1080p"` = **27.0** (residual ≥ 3).
  - `"big titty step sistinder match"` vs the Zarina Noir candidate = **80.0** (residual 3).

---

### Task 1: Generalize the foreign-title veto arm + raise the ratio gate

Change the veto's arming guard to fire for any title-less strong set and raise `_FOREIGN_TITLE_RATIO` to 40. Both edits land together: the incident (`{date, performer}`, ratio 36.4) needs the generalized arm *and* the raised constant to die, so a single atomic commit takes its test red → green.

**Files:**
- Modify: `scenehound/matcher.py` (module docstring foreign-title bullet ~lines 37–42; constant `_FOREIGN_TITLE_RATIO` line 62; veto comment + guard lines 196–214)
- Test: `tests/test_matcher.py` (append a new section)

**Interfaces:**
- Consumes: existing `score(scene, title, other_sites=frozenset(), date_skew_days=3) -> MatchScore`; `MatchScore(confidence: int, strong_signals: tuple[str, ...], veto: str | None, detail: dict[str, float])`; `SceneFingerprint(scene_id, site, site_aliases, date, title, performers)`.
- Produces: `MatchScore.veto == "foreign-title"` now also for `{date,performer}`, `{site,performer}`, and `{site,date,performer}` strong sets whose candidate has ≥3 distinct residual tokens at title ratio < 40. No signature change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matcher.py`:

```python
# --- foreign-title veto generalization: any title-less strong set ---
# 2026-07-15 production false grab #3 (GloryholeSecrets): {date, performer} both
# honest (Sydney Paige in both scenes; primary yyyy-mm-dd date is ±1) but the
# candidate names a different scene AND a different studio. The {site,date}-only
# arm did not fire. See docs/superpowers/specs/2026-07-15-foreign-title-veto-generalization-design.md

GLORYHOLE_SCENE = SceneFingerprint(
    scene_id=40,
    site="Shoplyfter Mylf",
    site_aliases=("Shoplyfter",),
    date=date(2024, 6, 15),
    title="Case No. 8002506 Bending the Right Way",
    performers=("Sydney Paige",),
)
GLORYHOLE_RELEASE = (
    "[GloryholeSecrets] Sydney Paige's First Glory Hole - Sydney Paige (2024-06-14) [2160p]"
)


def test_incident_date_performer_foreign_title_vetoes():
    # The candidate agrees on date (June 14, ±1 of June 15) and performer (Sydney
    # Paige) but its title names a different scene: 5 distinct residual tokens at
    # ratio 36.4 (< 40). Killed by the generalized arm.
    s = score(GLORYHOLE_SCENE, GLORYHOLE_RELEASE)
    assert s.veto == "foreign-title"
    assert s.strong_signals == ("date", "performer")
    assert s.confidence == 0


def test_date_performer_absence_still_matches():
    # Absence is not contradiction: a bare performer + date with no foreign title
    # tokens (residual 0) still clears on {date, performer}.
    s = score(SCENE, "Jane Doe (2026-07-07) 1080p")
    assert s.veto is None
    assert {"date", "performer"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_date_performer_legit_recall_matches():
    # The sole in-corpus title-less {date, performer} match: both performers +
    # date + partial title (ratio 76.5, residual 0). Must survive the new arm.
    s = score(SCENE, "Jane Doe & Mary Major - Latex Worship (07.07.2026) 2160p")
    assert s.veto is None
    assert s.confidence >= 75


def test_date_performer_near_exact_title_is_exempt():
    # A near-exact title promotes 'title' into strong → {date, performer, title};
    # "title" in strong means the gate is skipped and the scene is confirmed.
    s = score(SCENE, "Jane Doe - Latex Worship Session (2026-07-07) 1080p")
    assert s.veto is None
    assert "title" in s.strong_signals
    assert s.confidence >= 75


def test_site_date_performer_foreign_title_now_vetoes():
    # Three attributes agree (site + date + performer) but the title is foreign
    # (ratio 27.0, ≥3 residual). The 3-signal title-less set arms too.
    s = score(SCENE, "ThatFetishGirl Jane Doe - Wildly Different Bondage Clip (2026-07-07) 1080p")
    assert s.veto == "foreign-title"
    assert s.confidence == 0


def test_site_date_performer_corroborating_title_matches():
    # Same three attributes, but the title corroborates (ratio 76.5 ≥ 40): match.
    s = score(SCENE, "ThatFetishGirl Jane Doe - Latex Worship (2026-07-07) 1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_site_performer_corroborating_title_still_matches():
    # Generalization must not regress the existing {site, performer} path: the
    # Zarina Noir shape (residual 3 but ratio 80 ≥ 40) still matches.
    scene = SceneFingerprint(41, "Household Fantasy", (), date(2026, 7, 7),
                             "Big Titty Step-Sistinder Match", ("Zarina Noir",))
    s = score(scene, "[ScottStark-HouseholdFantasy] Zarina Noir - Big Titty Step Sister Tinder Match [1080p]")
    assert s.veto is None
    assert {"site", "performer"} <= set(s.strong_signals)
    assert s.confidence >= 75
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v -k "date_performer or site_date_performer or site_performer_corroborating"`
Expected: the two veto tests FAIL — `test_incident_date_performer_foreign_title_vetoes` and `test_site_date_performer_foreign_title_now_vetoes` (both currently score ≥75 with `veto is None`, because the arm only fires for `{site, date}`). The five no-veto tests PASS (they pin current behavior against regression — today those sets never reach the veto at all).

- [ ] **Step 3: Raise the ratio constant**

In `scenehound/matcher.py`, replace the `_FOREIGN_TITLE_RATIO` constant (line 62):

```python
_FOREIGN_TITLE_RATIO = 35     # below this, the candidate's own words read as a different scene
```

with:

```python
_FOREIGN_TITLE_RATIO = 40     # below this, the candidate's own words read as a different scene.
#                               Raised from 35 (2026-07-15 GloryholeSecrets grab: a generic scene
#                               title "case no … the right way" fuzz-aligns to 36.4 against foreign
#                               words — not real corroboration).
```

- [ ] **Step 4: Generalize the veto arm and its comment**

In `scenehound/matcher.py`, replace the foreign-title veto block (the comment at lines 196–203 plus the `if` guard at lines 204–214):

```python
    # --- foreign-title veto ---
    # {site, date} is the only strong pair with no content confirmation: on a
    # studio's own feed the site name discriminates nothing, and dates collide
    # across ambiguous stamps and same-day siblings. When the candidate carries
    # enough of its own words — beyond the scene's site, title, and performers —
    # with near-zero title similarity, it names a DIFFERENT scene. Absence is
    # not contradiction: a bare Site.YY.MM.DD release has no residual and
    # still matches on site+date.
    if (
        set(strong) == {"date", "site"}
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
        and (title_ratio is None or title_ratio < _FOREIGN_TITLE_RATIO)
    ):
        known = set(scene_ctoks)
        for name in (scene.site, *scene.site_aliases, *scene.performers):
            known.update(tokenize(name))
            known.add(squash(name))  # glued forms: "[FamilyTherapy]" is not foreign
        if sum(1 for t in set(cand_ctoks) if t not in known) >= _MIN_FOREIGN_RESIDUAL:
            return MatchScore(0, tuple(strong), "foreign-title", detail)
```

with:

```python
    # --- foreign-title veto ---
    # A title-less strong set (any pair or triple of site/date/performer, no
    # title match) confirms only attributes that can independently collide: the
    # site discriminates nothing on a studio's own feed, dates collide across
    # ambiguous stamps and same-day siblings, and a performer confirms a person,
    # not the specific scene (Sydney Paige is in many releases). When the
    # candidate carries enough of its own words — beyond the scene's site, title,
    # and performers — at near-zero title similarity, it names a DIFFERENT scene.
    # Only a title match (which would put "title" in strong) confirms the scene,
    # so a strong set containing "title" is exempt. Absence is not contradiction:
    # a bare Performer.YYYY.MM.DD or Site.YY.MM.DD release has no residual and
    # still matches.
    if (
        len(strong) >= 2
        and "title" not in strong
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
        and (title_ratio is None or title_ratio < _FOREIGN_TITLE_RATIO)
    ):
        known = set(scene_ctoks)
        for name in (scene.site, *scene.site_aliases, *scene.performers):
            known.update(tokenize(name))
            known.add(squash(name))  # glued forms: "[FamilyTherapy]" is not foreign
        if sum(1 for t in set(cand_ctoks) if t not in known) >= _MIN_FOREIGN_RESIDUAL:
            return MatchScore(0, tuple(strong), "foreign-title", detail)
```

(Only two lines of the guard change: `set(strong) == {"date", "site"}` becomes `len(strong) >= 2` + `and "title" not in strong`. The `known` construction and the distinct-residual test are unchanged.)

- [ ] **Step 5: Rewrite the module docstring bullet**

In `scenehound/matcher.py`, replace the foreign-title bullet in the module docstring (lines 37–42):

```
- Foreign-title veto: when site+date is the ENTIRE strong set, the scene
  title is distinctive, and the candidate carries >= 3 content tokens beyond
  the scene's site/title/performers at near-zero title similarity, the
  candidate names a different scene and is vetoed. Absence is not
  contradiction — a bare Site.YY.MM.DD release (no residual) still matches;
  2 residual tokens are routinely filler ("Bonus Scene") and forgiven.
```

with:

```
- Foreign-title veto: when the strong set is title-less (any pair or triple of
  site/date/performer, with no title match), the scene title is distinctive,
  and the candidate carries >= 3 DISTINCT content tokens beyond the scene's
  site/title/performers at title similarity below _FOREIGN_TITLE_RATIO (40),
  the candidate names a different scene and is vetoed. A title-less set confirms
  only colliding attributes — a performer confirms a person, not the scene — so
  a {date, performer} grab of the right person on the right day with a foreign
  title is a false positive (2026-07-15 GloryholeSecrets grab). A strong set
  containing "title" is exempt (the title confirms the scene). Absence is not
  contradiction — a bare Site.YY.MM.DD release (no residual) still matches;
  2 residual tokens are routinely filler ("Bonus Scene") and forgiven.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v -k "date_performer or site_date_performer or site_performer_corroborating"`
Expected: ALL PASS — the two veto tests now return `veto == "foreign-title"` / `confidence == 0`; the five no-veto tests still pass.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS. In particular the pre-existing foreign-title tests (`test_site_plus_date_with_foreign_title_now_vetoes`, `test_foreign_title_vetoes_site_date_pair`, `test_bare_site_date_release_still_matches`, `test_two_residual_filler_tokens_do_not_veto`, `test_fuzzy_title_overlap_defuses_foreign_veto`, `test_generic_scene_title_skips_foreign_veto`) still pass — the `{site, date}` arm is a subset of the generalized guard, and 35 → 40 keeps the FamilyTherapy `[14-07-25]` veto (28.6 < 40) and the `Bonus.Scene` match (residual 2 < 3). The corpus (`tests/test_corpus.py`) and losslessness (`test_lossless_*`) stay green — scoring-only change.

- [ ] **Step 8: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py
git commit -m "fix: foreign-title veto arms for any title-less strong set

The {site,date}-only arm missed the 2026-07-15 GloryholeSecrets grab: {date,
performer} both honest (right person, ±1 date) but the title named a different
scene at a different studio. Generalize the guard to len(strong)>=2 and
'title' not in strong, covering {date,performer}, {site,performer}, and
{site,date,performer}. Raise _FOREIGN_TITLE_RATIO 35->40 — the incident's
generic scene title fuzz-aligns to 36.4, above the old gate.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Corpus regression entry

The corpus is the accuracy ratchet: append the production mismatch so it can never silently return. It scores 75 and matches on `main`; Task 1 makes it `no_match`.

**Files:**
- Modify: `tests/fixtures/corpus.yaml` (append at end)
- Test: `tests/test_corpus.py` (parametrized — no code change)

**Interfaces:**
- Consumes: matcher behavior from Task 1. Corpus schema: `release` (str), `scene` (`site` / `aliases` / `date` / `title` / `performers`), `expect: match | no_match` (`match` ⇒ confidence ≥ 75; `no_match` ⇒ < 75).

- [ ] **Step 1: Append the entry**

At the end of `tests/fixtures/corpus.yaml`:

```yaml
# --- 2026-07-15 production false grab #3: {date, performer} with a contradicting title ---
# Sydney Paige really is in both scenes and the primary yyyy-mm-dd reading (June 14) is
# genuinely 1 day from the wanted June 15 — date (+40) and performer (+35) are both honest,
# so secondary-reading demotion (Layer A) does not apply. But the release plainly names a
# different scene AND a different studio ([GloryholeSecrets] != Shoplyfter Mylf). The
# {site,date}-only foreign-title veto did not arm for {date,performer}; the generalized
# title-less arm kills it (5 distinct residual tokens, ratio 36.4 < 40).
- release: "[GloryholeSecrets] Sydney Paige's First Glory Hole - Sydney Paige (2024-06-14) [2160p]"
  scene:
    site: "Shoplyfter Mylf"
    aliases: ["Shoplyfter"]
    date: 2024-06-15
    title: "Case No. 8002506 Bending the Right Way"
    performers: ["Sydney Paige"]
  expect: no_match
```

- [ ] **Step 2: Run the corpus suite**

Run: `.venv/bin/python -m pytest tests/test_corpus.py -v`
Expected: ALL PASS, including the new id. (The candidate never names *Shoplyfter Mylf*, so `site` is not a strong signal — the strong set is `{date, performer}` and the entry reproduces the false grab, now vetoed.)

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/corpus.yaml
git commit -m "test: corpus entry for the 2026-07-15 GloryholeSecrets {date,performer} grab

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: UI trace copy

The current `foreign-title` copy is now factually wrong for the new arms — it says the performers don't match and studio + date can't carry it, but in the `{date, performer}` incident the performers **do** match and the studio does **not**. Rewrite it pair-agnostic. Rendering copy only — no Python change, no JS test infra exists.

**Files:**
- Modify: `scenehound/static/ui.html` (`VETO_TEXT` map, line 121)

**Interfaces:**
- Consumes: `veto == "foreign-title"` (Task 1). No new detail keys.

- [ ] **Step 1: Rewrite the veto copy**

In `scenehound/static/ui.html`, replace the `"foreign-title"` entry in `VETO_TEXT` (line 121):

```js
  "foreign-title": "Rejected: the release names a different scene — its title and performers don't match, and studio + date alone can't carry it.",
```

with:

```js
  "foreign-title": "Rejected: the release's title names a different scene — the signals that do agree (studio, date, or performer) can't identify it without a matching title.",
```

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -m pytest` (full suite — guards against accidental Python edits)
Expected: ALL PASS.
Then: `grep -n "foreign-title" scenehound/static/ui.html`
Expected: the new pair-agnostic string is present inside `VETO_TEXT`, and no longer mentions "studio + date alone".

- [ ] **Step 3: Commit**

```bash
git add scenehound/static/ui.html
git commit -m "feat: pair-agnostic UI copy for the generalized foreign-title veto

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Out of scope (per spec)

- Version bump / release tagging — PR #20, this work, and the multi-grab RSS badge follow-up ship as ONE grouped release; tag only when all three are merged.
- Multi-grab RSS badge slot (`observe.py record_grab` single `grabbed_guid` overwrite) — separate follow-up.
- Studio-contradiction as its own signal — residual mass already catches the foreign studio.
- `query_planner.py` / `parse_query_term`; `import_completer.py`; accent transliteration in `normalize.py`.
- `scenehound/dates.py` — reading ranks shipped in PR #20; this incident's date is a genuine primary-reading match, so no date-layer change.
