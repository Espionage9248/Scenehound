# Secondary-Reading Date Demotion + Foreign-Title Veto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the matcher fabricating strong date signals from alternate readings of ambiguous release dates (Layer A), and veto site+date-only matches whose candidate plainly names a different scene (Layer B) — the 2026-07-15 production false grab.

**Architecture:** `extract_dates` in `scenehound/dates.py` gains reading ranks (primary = dominant convention per format, secondary = alternate orderings); `scenehound/matcher.py` lets only primary readings become strong date signals and adds a `foreign-title` contradiction veto for the {site, date} strong pair. The wanted-index pre-filter keeps using the union so it stays a lossless superset. UI trace gets copy for both new behaviors.

**Tech Stack:** Python 3.12+ (CI matrix 3.12 & 3.14), pytest, rapidfuzz. Venv at `.venv/` — run tests as `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-secondary-date-foreign-title-design.md`

## Global Constraints

- `scenehound/matcher.py` and `scenehound/dates.py` are pure functions — no I/O, no config reads; tunables are module-level constants (no new config entries).
- `tests/fixtures/corpus.yaml` is the accuracy ratchet: every entry must keep passing; every production mismatch gets appended there.
- The wanted-index pre-filter must remain a lossless superset of anything the matcher can accept (existing `test_lossless_*` tests encode this).
- Commit messages use conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`) and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Run the FULL suite (`.venv/bin/python -m pytest`) before every commit, not just the file you touched.
- Pinned rapidfuzz `token_set_ratio` values these tasks rely on (already verified): `slut training day` vs the incident candidate = 28.6; `latex worship session` vs `thatfetishgirl unrelated clip name` = 32.7; `the massage lesson` vs `familytherapyxxx bonus scene` = 34.8; `latex worship session` vs `thatfetishgirl latex worship compilation` = 76.5.

---

### Task 1: Reading-ranked `extract_dates` (behavior-preserving refactor)

`extract_dates` currently returns a flat `frozenset[date]` mixing all readings of ambiguous formats. Give it ranks; update both callers to use `.all` so behavior is unchanged and the suite stays green. Layer A (Task 2) then consumes the ranks.

**Files:**
- Modify: `scenehound/dates.py` (function `extract_dates`, lines 60–80)
- Modify: `scenehound/matcher.py:120` (caller — mechanical `.all`)
- Modify: `scenehound/wanted_index.py:93` (caller — mechanical `.all`)
- Test: `tests/test_dates.py`, `tests/test_wanted_index.py`

**Interfaces:**
- Produces: `ExtractedDates` frozen dataclass in `scenehound.dates` with fields `primary: frozenset[date]`, `secondary: frozenset[date]` (disjoint — primary wins overlap) and property `all -> frozenset[date]` (union). `extract_dates(text: str) -> ExtractedDates`. Rank per format: `yy.mm.dd` primary for 2-digit triples (`dd.mm.yy`, `mm.dd.yy` secondary); `yyyy.mm.dd` primary (`yyyy.dd.mm` secondary); `dd.mm.yyyy` primary (`mm.dd.yyyy` secondary). Task 2 consumes `.primary`/`.secondary`; `wanted_index` consumes `.all`.

- [ ] **Step 1: Update existing tests and add rank tests**

In `tests/test_dates.py`, replace the four `extract_dates` tests (lines 28–50) with:

```python
def test_extract_iso_and_dotted_are_primary():
    assert date(2026, 7, 7) in extract_dates("Site.2026-07-07.Title").primary
    assert date(2026, 7, 7) in extract_dates("Site 2026.07.07 Title").primary


def test_extract_two_digit_year_scene_format_primary():
    # 26.07.05 → yy.mm.dd is the dominant scene convention
    ds = extract_dates("Site.26.07.05.Title.XXX")
    assert date(2026, 7, 5) in ds.primary


def test_extract_dmy_primary_mdy_secondary_four_digit_year():
    ds = extract_dates("released 12-07-2026 in HD")
    assert date(2026, 7, 12) in ds.primary     # dd.mm.yyyy, dominant
    assert date(2026, 12, 7) in ds.secondary   # mm.dd.yyyy, alternate
    assert ds.all == ds.primary | ds.secondary


def test_extract_ignores_resolutions_and_garbage():
    assert extract_dates("Site.Title.1080p.x265").all == frozenset()
    assert extract_dates("no dates at all").all == frozenset()


def test_extract_implausible_years_dropped():
    assert extract_dates("Thing.1085-01-01.wat").all == frozenset()


def test_triple2_alternate_readings_are_secondary():
    # The 2026-07-15 false-grab token: [26-07-14] is 2026-07-14 in the
    # dominant yy-mm-dd convention; 2014-07-26 only via the dd-mm-yy alternate.
    ds = extract_dates("[FamilyTherapy] Alexa Chains - Goth Latina [26-07-14] [1080p]")
    assert date(2026, 7, 14) in ds.primary
    assert date(2014, 7, 26) in ds.secondary
    assert date(2014, 7, 26) not in ds.primary


def test_primary_wins_dedup_across_tokens():
    # 2026-07-05 is primary via the ISO token AND secondary via dd.mm.yy of
    # the triple; the sets must stay disjoint with primary winning.
    ds = extract_dates("Site.2026-07-05.and.05-07-26.Clip")
    assert date(2026, 7, 5) in ds.primary
    assert date(2026, 7, 5) not in ds.secondary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dates.py -v`
Expected: FAIL — `AttributeError: 'frozenset' object has no attribute 'primary'` (and `.all`).

- [ ] **Step 3: Implement `ExtractedDates` and the ranked builder**

In `scenehound/dates.py`, replace `extract_dates` (lines 60–80) with:

```python
@dataclass(frozen=True)
class ExtractedDates:
    """Dates found in a release title, ranked by reading plausibility.

    primary: produced by the dominant convention of their format (yy.mm.dd
    for two-digit triples, yyyy.mm.dd, dd.mm.yyyy). secondary: reachable only
    via an alternate reading of an ambiguous ordering. Disjoint — a date
    reachable both ways is primary."""

    primary: frozenset[date]
    secondary: frozenset[date]

    @property
    def all(self) -> frozenset[date]:
        return self.primary | self.secondary


def extract_dates(text: str) -> ExtractedDates:
    """Every plausible date found in a release title, across formats, ranked
    by reading. The matcher lets only primary dates become strong signals
    (a 26-07-14 release must not strongly match a 2014-07-26 scene); the
    wanted-index pre-filter uses .all to stay a lossless superset."""
    prim: set[date] = set()
    sec: set[date] = set()
    for m in _YMD4.finditer(text):
        y, b, c = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(y, b, c):  # yyyy.mm.dd (dominant)
            prim.add(d)
        if d := _valid(y, c, b):  # yyyy.dd.mm
            sec.add(d)
    for m in _XY4.finditer(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(y, b, a):  # dd.mm.yyyy (dominant; parse_query_term precedent)
            prim.add(d)
        if d := _valid(y, a, b):  # mm.dd.yyyy
            sec.add(d)
    for m in _TRIPLE2.finditer(text):
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(_expand_two_digit_year(a), b, c):  # yy.mm.dd (dominant scene convention)
            prim.add(d)
        for y2, mo, dy in ((c, b, a), (c, a, b)):  # dd.mm.yy, mm.dd.yy
            if d := _valid(_expand_two_digit_year(y2), mo, dy):
                sec.add(d)
    return ExtractedDates(frozenset(prim), frozenset(sec - prim))
```

(`dataclass` is already imported at the top of the file.)

- [ ] **Step 4: Update both callers mechanically (behavior unchanged)**

`scenehound/matcher.py:120`:

```python
    title_dates = extract_dates(title).all
```

`scenehound/wanted_index.py:93`:

```python
        for d in extract_dates(title).all:
```

- [ ] **Step 5: Add the lossless pre-filter test**

Append to `tests/test_wanted_index.py`:

```python
def test_lossless_secondary_reading_date_bucket():
    # 26-07-14 read as dd.mm.yy is 2014-07-26; the pre-filter must keep scenes
    # reachable only via that alternate reading (matcher veto-forgiveness and
    # skew handling depend on seeing the candidate at all).
    scene = SceneFingerprint(55, "ExampleSite", (), date(2014, 7, 26), "Foo Bar Baz", ())
    idx = WantedIndex([scene])
    assert scene in idx.candidates_for_title("Random.Words.26-07-14.Clip.1080p")
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS (refactor is behavior-preserving; corpus untouched).

- [ ] **Step 7: Commit**

```bash
git add scenehound/dates.py scenehound/matcher.py scenehound/wanted_index.py tests/test_dates.py tests/test_wanted_index.py
git commit -m "refactor: extract_dates returns reading-ranked ExtractedDates

Primary = dominant convention per format (yy.mm.dd triples, yyyy.mm.dd,
dd.mm.yyyy); secondary = alternate orderings. Callers use .all — behavior
unchanged; the matcher starts consuming ranks in the next commit.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Layer A — secondary-reading date demotion

Only a primary-reading date within ±1 day is a strong signal. A secondary-only match contributes zero points, forgives the date-mismatch veto, and is traced via `detail["date_secondary_reading"]` (added after summation, like `date_skew_days`, so it can't leak a point into the total).

**Files:**
- Modify: `scenehound/matcher.py` (date block lines 119–128, post-total lines 174–179, module docstring)
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `ExtractedDates.primary` / `.secondary` / `.all` from Task 1.
- Produces: `MatchScore.detail` may carry `date_secondary_reading: 1.0` (trace metadata, not points — Task 5's UI marker keys off it). `strong_signals` contains `"date"` only for primary-reading hits.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_matcher.py`:

```python
# --- secondary-reading date demotion (2026-07-15 production false grab) ---

INCIDENT_SCENE = SceneFingerprint(
    scene_id=30,
    site="Family Therapy XXX",
    site_aliases=("Family Therapy",),
    date=date(2014, 7, 25),
    title="Slut Training Day",
    performers=(),
)
INCIDENT_RELEASE = (
    "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [26-07-14] [1080p]"
)


def test_incident_secondary_reading_date_is_not_strong():
    # Production false grab 2026-07-15: [26-07-14] is 2026-07-14 in the
    # uploader's yy-mm-dd; the dd-mm-yy alternate (2014-07-26) sat 1 day from
    # the scene and fabricated a strong date next to the site hit (40+35=75).
    # Demoted: site alone stays capped under threshold.
    s = score(INCIDENT_SCENE, INCIDENT_RELEASE)
    assert s.veto is None                      # alternate reading forgives the veto
    assert "date" not in s.strong_signals
    assert s.strong_signals == ("site",)
    assert s.confidence < 75


def test_secondary_reading_traced_not_scored():
    s = score(INCIDENT_SCENE, INCIDENT_RELEASE)
    assert s.detail["date_secondary_reading"] == 1.0
    assert "date" not in s.detail  # zero points from the date


def test_secondary_reading_forgives_veto_for_ddmmyy_uploaders():
    # Honest dd.mm.yy uploader: the dominant yy.mm.dd reading of [14-07-26]
    # contradicts (2014-07-26), the alternate matches exactly (2026-07-14) →
    # no veto; the match is carried by site+performer+title.
    scene = SceneFingerprint(31, "Some Site", (), date(2026, 7, 14),
                             "Latex Worship Session", ("Jane Doe",))
    s = score(scene, "[SomeSite] Jane Doe - Latex Worship Session [14-07-26] [1080p]")
    assert s.veto is None
    assert "date" not in s.strong_signals
    assert {"site", "performer"} <= set(s.strong_signals)
    assert s.detail["date_secondary_reading"] == 1.0
    assert s.confidence >= 75


def test_secondary_reading_contributes_no_points():
    # Same construction as test_forgiven_date_contributes_no_points: keep the
    # total below the min(100, …) clamp so a leaked point would show.
    scene = SceneFingerprint(31, "Some Site", (), date(2026, 7, 14),
                             "Latex Worship Session", ("Jane Doe",))
    dated = score(scene, "[SomeSite] Jane Doe - Solo Clip [14-07-26]")
    undated = score(scene, "[SomeSite] Jane Doe - Solo Clip")
    assert dated.veto is None
    assert dated.confidence == undated.confidence < 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v -k "secondary_reading or incident"`
Expected: all four FAIL — the incident scores 75 with `strong == ("date", "site")` and no `date_secondary_reading` key.

- [ ] **Step 3: Implement the ranked date block**

In `scenehound/matcher.py`, replace the date block (lines 119–128, starting `# --- date ---`) with:

```python
    # --- date ---
    extracted = extract_dates(title)
    date_off: int | None = None  # smallest days-off when no reading is within ±1
    date_secondary = False
    if extracted.all:
        if any(abs((d - scene.date).days) <= 1 for d in extracted.primary):
            strong.append("date")
            detail["date"] = STRONG_DATE
        elif any(abs((d - scene.date).days) <= 1 for d in extracted.secondary):
            # Matched only via an alternate reading of an ambiguous ordering
            # (e.g. dd.mm.yy of a yy.mm.dd stamp): forgives the veto, never
            # strong, contributes no points. Flag recorded after summation.
            date_secondary = True
        else:
            date_off = min(abs((d - scene.date).days) for d in extracted.all)
```

(The Task 1 mechanical `title_dates = extract_dates(title).all` line is subsumed by this block.)

Then extend the post-summation trace lines (currently lines 174–179) to:

```python
    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    if date_off is not None:
        detail["date_skew_days"] = float(date_off)  # trace metadata, not points
    if date_secondary:
        detail["date_secondary_reading"] = 1.0  # trace metadata, not points
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
```

- [ ] **Step 4: Extend the module docstring**

In the `scenehound/matcher.py` module docstring, after the date-veto bullet (ends `…not addressed in this fix.` / the `date_skew_days` bullet ending line 29), add:

```
- Only a PRIMARY-reading date — the dominant convention of its format
  (yy.mm.dd for two-digit triples, yyyy.mm.dd, dd.mm.yyyy) — can be a strong
  signal. A date matched only via an alternate reading of an ambiguous
  ordering forgives the date veto but is never strong and contributes no
  points (detail["date_secondary_reading"] traces it): a [26-07-14] release
  must not strongly match a 2014-07-26 scene by cherry-picking the dd.mm.yy
  reading (2026-07-15 production false grab).
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS. Every pre-existing date test uses a dominant-convention stamp, so nothing else moves; the corpus stays green.

- [ ] **Step 6: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py
git commit -m "fix: only primary-reading dates can be strong signals

A date matched only via an alternate reading of an ambiguous ordering
(dd.mm.yy of a yy.mm.dd stamp) forgives the date veto but scores nothing —
the 2026-07-15 false grab cherry-picked 2014-07-26 out of [26-07-14].

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Layer B — foreign-title veto

When {site, date} is the entire strong set and the candidate carries ≥3 content tokens beyond the scene's site/title/performers with near-zero title similarity, it names a *different* scene → hard veto `"foreign-title"`. Zero residual (bare `Site.YY.MM.DD` releases) still matches — absence is not contradiction.

**Files:**
- Modify: `scenehound/matcher.py` (title block, new constants, new veto block, docstring)
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `strong` list, `scene_ctoks`/`cand_ctoks`, hoisted `title_ratio` (this task renames the local `ratio`).
- Produces: `MatchScore.veto` may be `"foreign-title"` (Task 5 adds UI copy for it). Module constants `_FOREIGN_TITLE_RATIO = 35`, `_MIN_FOREIGN_RESIDUAL = 3`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_matcher.py`, **replace** `test_site_plus_date_without_title_overlap_still_matches` (lines 184–191) with:

```python
def test_site_plus_date_with_foreign_title_now_vetoes():
    # Pre-2026-07-15 this pinned "site+date, no title overlap → 75". The
    # production false grab proved the class dangerous: three residual content
    # tokens naming a different clip are contradiction, not absence.
    scene = SceneFingerprint(27, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ())
    s = score(scene, "ThatFetishGirl.2026-07-07.Unrelated.Clip.Name.1080p")
    assert s.veto == "foreign-title"
    assert s.confidence < 75
```

Then append:

```python
# --- foreign-title veto (contradiction, not absence) ---


def test_foreign_title_vetoes_site_date_pair():
    # Same class as the incident but via a primary-reading date collision:
    # site+date agree, yet the candidate names a different scene outright.
    s = score(
        INCIDENT_SCENE,
        "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [14-07-25] [1080p]",
    )
    assert s.veto == "foreign-title"
    assert s.confidence == 0


def test_bare_site_date_release_still_matches():
    # Absence is not contradiction: zero residual tokens → site+date clears.
    s = score(INCIDENT_SCENE, "FamilyTherapy.14.07.25.XXX.1080p")
    assert s.veto is None
    assert {"date", "site"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_two_residual_filler_tokens_do_not_veto():
    # "Bonus Scene"-style filler (2 residual tokens, ratio 34.8) is
    # absence-adjacent, not a foreign title — the decorative-xxx corpus entry
    # and losslessness test depend on this staying a match.
    scene = SceneFingerprint(54, "Family Therapy", ("Family Therapy XXX",),
                             date(2026, 7, 7), "The Massage Lesson", ("Jane Doe",))
    s = score(scene, "FamilyTherapyXXX.26.07.07.Bonus.Scene.XXX.1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_fuzzy_title_overlap_defuses_foreign_veto():
    # Partial title overlap (ratio 76.5) corroborates: not a foreign title.
    s = score(SCENE, "ThatFetishGirl.2026-07-07.Latex.Worship.Compilation.1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_generic_scene_title_skips_foreign_veto():
    # A 1-content-token scene title can't establish contradiction.
    scene = SceneFingerprint(33, "That Fetish Girl", (), date(2026, 7, 7), "Casting", ())
    s = score(scene, "ThatFetishGirl.2026-07-07.Totally.Different.Words.1080p")
    assert s.veto is None
    assert s.confidence >= 75
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_matcher.py -v -k "foreign or bare_site or filler or generic_scene"`
Expected: the two veto tests FAIL (they currently match at ≥75 with no veto); the no-veto tests PASS (they pin current behavior against regression).

- [ ] **Step 3: Implement the veto**

In `scenehound/matcher.py`:

(a) Add constants after `_TITLE_RATIO_GATE = 60` (line 48):

```python
_FOREIGN_TITLE_RATIO = 35     # below this, the candidate's own words read as a different scene
_MIN_FOREIGN_RESIDUAL = 3     # candidate content tokens beyond scene site/title/performers;
#                               2 is routinely filler ("Bonus Scene"), 3+ is a foreign title
```

(b) Hoist the title ratio. Replace the title-similarity block (lines 145–163) with (only changes: `title_ratio` initialised before the `if`, `ratio` renamed):

```python
    # --- title similarity ---
    scene_ctoks = content_tokens(scene.title)
    cand_ctoks = content_tokens(title)
    title_ratio: float | None = None
    if scene_ctoks and cand_ctoks:
        cand_ctok_set = set(cand_ctoks)
        near_exact = len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS and all(
            t in cand_ctok_set for t in scene_ctoks
        )
        # Title is strong ONLY alongside a site or performer strong signal (the
        # site/performer blocks run before this one, so `strong` is populated).
        # This encodes the design's valid strong-pairs (date+performer, site+date,
        # site+performer, site+title): title must never pair with date alone.
        if near_exact and ("site" in strong or "performer" in strong):
            strong.append("title")
            detail["title"] = STRONG_TITLE
        else:
            title_ratio = fuzz.token_set_ratio(" ".join(scene_ctoks), " ".join(cand_ctoks))
            if title_ratio >= _TITLE_RATIO_GATE:
                detail["title"] = title_ratio / 100.0 * TITLE_MAX
```

(c) Insert the veto between the date-veto block and `total = sum(detail.values())` (date-mismatch keeps precedence when both apply):

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
        if sum(1 for t in cand_ctoks if t not in known) >= _MIN_FOREIGN_RESIDUAL:
            return MatchScore(0, tuple(strong), "foreign-title", detail)
```

(d) Extend the module docstring after the Task 2 bullet:

```
- Foreign-title veto: when site+date is the ENTIRE strong set, the scene
  title is distinctive, and the candidate carries >= 3 content tokens beyond
  the scene's site/title/performers at near-zero title similarity, the
  candidate names a different scene and is vetoed. Absence is not
  contradiction — a bare Site.YY.MM.DD release (no residual) still matches;
  2 residual tokens are routinely filler ("Bonus Scene") and forgiven.
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS — including `tests/test_corpus.py` (the `Bonus.Scene` xxx-toggle entry survives via the residual guard) and `tests/test_wanted_index.py::test_lossless_xxx_toggled_site_alias_both_directions` (asserts that same release still scores ≥75).

- [ ] **Step 5: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py
git commit -m "feat: foreign-title veto for site+date-only matches

A candidate carrying >=3 content tokens beyond the scene's site, title, and
performers at near-zero title similarity names a different scene — veto it.
Bare Site.YY.MM.DD releases (no residual) still match on site+date.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Corpus regression entries

The corpus is the accuracy ratchet: append the production mismatch (Layer A kill) and its primary-reading sibling (Layer B kill).

**Files:**
- Modify: `tests/fixtures/corpus.yaml` (append at end)
- Test: `tests/test_corpus.py` (parametrized — no code change)

**Interfaces:**
- Consumes: matcher behavior from Tasks 2–3. Corpus schema: `release` (str), `scene` (`site`/`aliases`/`date`/`title`/`performers`), `expect: match|no_match`.

- [ ] **Step 1: Append the entries**

At the end of `tests/fixtures/corpus.yaml`:

```yaml
# --- 2026-07-15 production false grab: ambiguous yy-mm-dd cherry-picked as dd-mm-yy ---
# [26-07-14] is 2026-07-14 (uploader's yy-mm-dd); the dd-mm-yy alternate 2014-07-26
# sat 1 day from the wanted scene and fabricated a strong date beside the site hit
# (40+35=75, grabbed and imported). The true scene wasn't on StashDB yet — the only
# correct outcome is NO match. Killed by secondary-reading demotion.
- release: "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [26-07-14] [1080p]"
  scene: &slut_training
    site: "Family Therapy XXX"
    aliases: ["Family Therapy"]
    date: 2014-07-25
    title: "Slut Training Day"
  expect: no_match

# Same candidate with a primary-reading date collision (yy-mm-dd lands exactly on the
# scene's date): site+date agree but the release plainly names a different scene.
# Killed by the foreign-title veto.
- release: "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [14-07-25] [1080p]"
  scene: *slut_training
  expect: no_match
```

- [ ] **Step 2: Run the corpus suite**

Run: `.venv/bin/python -m pytest tests/test_corpus.py -v`
Expected: ALL PASS, including the two new ids.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/corpus.yaml
git commit -m "test: corpus entries for the 2026-07-15 false grab (both date readings)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: UI trace copy

Surface both new behaviors in the match trace: rejection copy for `foreign-title`, a warning marker for secondary-reading dates. `observe.py` already records the full `detail` dict — rendering only, no Python changes, no JS test infra exists.

**Files:**
- Modify: `scenehound/static/ui.html` (`VETO_TEXT` map ~line 118, `whyLines` ~line 132)

**Interfaces:**
- Consumes: `veto == "foreign-title"` (Task 3), `detail.date_secondary_reading` (Task 2).

- [ ] **Step 1: Add the veto copy**

In `VETO_TEXT` (`scenehound/static/ui.html:118`), after the `"site-mismatch"` entry:

```js
  "foreign-title": "Rejected: the release names a different scene — its title and performers don't match, and studio + date alone can't carry it.",
```

- [ ] **Step 2: Add the secondary-reading marker**

In `whyLines`, directly after the `date_skew_days` marker (lines 132–133):

```js
  if (c.detail.date_secondary_reading != null)
    out.push(`<span class="warn">⚠ Date matches only via an alternate reading of an ambiguous format (e.g. dd-mm-yy vs yy-mm-dd) — not counted as a strong signal.</span>`);
```

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -m pytest` (full suite — guards against accidental Python edits)
Expected: ALL PASS.
Then: `grep -n "foreign-title\|date_secondary_reading" scenehound/static/ui.html`
Expected: both strings present, veto copy inside `VETO_TEXT`, marker inside `whyLines`.

- [ ] **Step 4: Commit**

```bash
git add scenehound/static/ui.html
git commit -m "feat: UI trace copy for foreign-title veto and secondary-reading dates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Out of scope (per spec)

- `query_planner.py` / `parse_query_term` — separate, already-ordered path.
- `import_completer.py` — gates purely on confidence.
- Version bump / release tagging — done at release time, not in this plan.
- Accent transliteration in `normalize.py`; performer-roster contradiction.
