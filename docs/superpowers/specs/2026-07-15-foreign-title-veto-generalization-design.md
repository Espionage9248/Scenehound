# Foreign-title veto generalization — arm on any title-less strong pair

**Date:** 2026-07-15
**Status:** Approved for planning
**Predecessor:** `docs/superpowers/specs/2026-07-15-secondary-date-foreign-title-design.md`
(PR #20, merged as `9cad57c`) — reuse its vocabulary and veto rationale.

## Problem

The third production false grab of 2026-07-15, **not** fixed by PR #20 (verified
on post-merge `main`: still scores exactly **75** and matches). RSS sync grabbed

```
[GloryholeSecrets] Sydney Paige's First Glory Hole - Sydney Paige (2024-06-14) [2160p]
```

for the wanted scene *Shoplyfter Mylf / 2024-06-15 / "Case No. 8002506 Bending
the Right Way" / performers ("Sydney Paige",)*.

Unlike the two PR #20 incidents there is **no date cherry-picking**: the primary
`yyyy-mm-dd` reading (June 14) is genuinely 1 day from the scene's June 15, and
Sydney Paige really is in both scenes. So date (+40) and performer (+35) are
**both honest strong signals** — Layer A (secondary-reading demotion) does not
apply and cannot.

What fails is the same contradiction-blindness PR #20's Layer B was built to
close, but for a strong pair Layer B does not cover. The candidate plainly names
a different scene *and* a different studio (`[GloryholeSecrets]` ≠ *Shoplyfter
Mylf*), yet the foreign-title veto only arms when `set(strong) == {"site",
"date"}`. Here the strong set is `{"date", "performer"}`, so the veto never
evaluates and the match clears threshold.

PR #20's rationale for scoping Layer B to `{site, date}` was that *site+date
carries no content confirmation while performer does*. This grab is corpus proof
that the rationale was too strong: **a performer signal confirms that a person
is present, not that this is the specific scene.** Sydney Paige appears in many
releases; her presence plus an honest ±1 date does not identify the scene when
the title names a different one. The same holds for every strong pair whose
signals are attributes that can independently collide — `{site, date}`,
`{date, performer}`, `{site, performer}`, and the three-way `{site, date,
performer}`. Only a **title** match confirms the specific scene, and only when
title is in the strong set is the candidate corroborated.

Live confirmation on post-merge `main`:

```
confidence 75   strong ('date', 'performer')   veto None
detail {'date': 40, 'performer': 35}
```

## Decision

Generalize Layer B's arming condition from the single enumerated pair
`{site, date}` to **any title-less strong set that clears the two-strong-signal
rule** — `len(strong) >= 2 and "title" not in strong`. The residual/known-token
machinery and the "absence is not contradiction" principle are unchanged; only
the set of strong configurations that *reach* the veto widens.

Rejected alternatives:

- **Enumerate `{date, performer}` as a third named pair, leave `{site,
  performer}` and `{site, date, performer}` unarmed.** Rejected: the "a
  colliding attribute is not scene confirmation" argument applies uniformly to
  every title-less set. Enumerating pair-by-pair invites a fourth incident for
  the next uncovered pair. The generalized form is also simpler code (one
  condition, not a growing tuple of sets) and corpus-clean.
- **Lower `{date, performer}` points / raise threshold.** Rejected for the same
  reason PR #20 rejected it for `{site, date}`: both signals are deliberately
  valid strong signals and a bare `Performer.YYYY.MM.DD` release with no residual
  content is a legitimate match. Punishing the honest case to catch the
  dishonest one violates the "absence is not contradiction" invariant.
- **Add a studio-contradiction signal** (the candidate names a *recognizable
  different* studio). Rejected / out of scope, as in PR #20: the foreign studio
  token (`gloryholesecrets`) is already counted as residual mass, and the veto
  fires on residual + low title similarity without needing to model it as its
  own contradiction. Revisit only with corpus evidence that residual mass alone
  under-catches.

### The ratio constant must change (not optional)

Extending the arm alone does **not** kill the incident. Its fuzzy title ratio is
**36.4** — *above* the current `_FOREIGN_TITLE_RATIO = 35` — so the ratio gate
reads the title as corroborating and blocks the veto. Verified: with the arm
generalized but the constant left at 35, the incident still scores 75.

The 36.4 is fuzzy character-alignment noise between the scene title's generic /
stopword tokens (`case no … the right way`) and arbitrary foreign words; it is
not real corroboration (removing the shared performer's name from the candidate
*raises* the ratio to 37.5, confirming the performer name is not the source).
`35` was tuned against a single incident whose ratio was 28.6; it is simply too
tight for a generic scene title. Raise **`_FOREIGN_TITLE_RATIO` 35 → 40.**

Corpus-wide separation is clean at 40: every genuinely foreign title sits ≤ 37.5,
every corroborating title sits ≥ 60. The change is verified safe for the existing
`{site, date}` arm too — the PR #20 corpus entry `[FamilyTherapy] … [14-07-25]`
still vetoes (28.6 < 40), and `FamilyTherapyXXX.26.07.07.Bonus.Scene` still
matches (residual 2 < 3, ratio-independent).

## Design

Scope note: this is a **strictly smaller change than PR #20** — `scenehound/matcher.py`
only (one gate condition, one constant), plus one corpus entry, the module
docstring, and one line of UI copy. **No `scenehound/dates.py` change**: reading
ranks already shipped in PR #20, and the incident's date is a genuine
primary-reading ±1 match, so Layer A neither applies nor needs touching.

### 1. Generalized arm condition (`scenehound/matcher.py`)

In the foreign-title veto block, replace the arm guard

```python
    if (
        set(strong) == {"date", "site"}
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
        and (title_ratio is None or title_ratio < _FOREIGN_TITLE_RATIO)
    ):
```

with

```python
    if (
        len(strong) >= 2
        and "title" not in strong
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
        and (title_ratio is None or title_ratio < _FOREIGN_TITLE_RATIO)
    ):
```

`len(strong) >= 2 and "title" not in strong` is exactly the set of title-less
strong configurations that clear the two-strong-signal rule. Because the only
strong signals are `date`, `site`, `performer`, `title`, this is precisely:
`{site, date}`, `{date, performer}`, `{site, performer}`, and `{site, date,
performer}`. The `>= 2` guard keeps the veto scoped to candidates that would
otherwise clear threshold — a single-signal candidate is already capped under 75
and does not warrant a "names a different scene" rejection in the trace.

Everything below the guard — the `known` set construction (tokenize + squash of
scene site, aliases, and every performer, plus scene title content tokens) and
the `sum(1 for t in set(cand_ctoks) if t not in known) >= _MIN_FOREIGN_RESIDUAL`
distinct-residual test — is **unchanged**. The distinctness (`set(cand_ctoks)`)
and the `3` are the PR #20 final-review fixes and must be preserved.

### 2. Constant (`scenehound/matcher.py`)

```python
_FOREIGN_TITLE_RATIO = 40     # below this, the candidate's own words read as a
#                               different scene. Raised from 35 (2026-07-15
#                               GloryholeSecrets grab: a generic scene title
#                               fuzz-aligns to 36.4 against foreign words).
```

`_MIN_FOREIGN_RESIDUAL = 3` is unchanged. Constants stay module-level — no config
entry (consistent with PR #20; earns a config knob only from a future corpus
argument, as `date_skew_days` did).

### 3. Behaviour table

| Strong set | Candidate | Residual (distinct) | Ratio | Outcome |
|---|---|---|---|---|
| `{date, performer}` | GloryholeSecrets incident | 5 | 36.4 | **veto `foreign-title`** |
| `{date, performer}` | `Jane Doe (2026-07-07)` (bare) | 0 | — | match (absence) |
| `{date, performer}` | `Jane Doe & Mary Major - Latex Worship (07.07.2026)` | 0 | 76.5 | match (corroborates + absence) |
| `{date, performer, title}` | near-exact title present | n/a | n/a | match — title in strong, gate skipped |
| `{site, performer}` | Zarina Noir corpus entry | 3 | 80.0 | match — ratio corroborates |
| `{site, date, performer}` | foreign title, ≥3 residual | ≥3 | < 40 | **veto `foreign-title`** |
| `{site, date, performer}` | corroborating title (ratio ≥ 40) | any | ≥ 40 | match |
| `{site, date}` | `[FamilyTherapy] … [14-07-25]` (PR #20) | 6 | 28.6 | veto `foreign-title` (unchanged) |
| `{site, date}` | `FamilyTherapyXXX.26.07.07.Bonus.Scene` | 2 | 34.8 | match (residual 2 < 3, unchanged) |

### 4. Invariants preserved (all verified by full-corpus simulation — 0 regressions)

- **Absence is not contradiction.** A bare `Performer.YYYY.MM.DD` release with no
  residual content still matches at 75. The residual gate (≥ 3 distinct) is what
  separates contradiction from absence, and it applies identically on the new
  arms.
- **`{date, performer, title}` auto-exempt.** A near-exact title promotes `title`
  into `strong`; `"title" not in strong` then skips the gate. The specific scene
  is confirmed, so the veto must not fire (verified: matches at 100).
- **date-mismatch veto keeps precedence.** The date-mismatch early-return runs
  upstream of the foreign-title block; both vetoes remain mutually exclusive by
  construction.
- **The wanted-index pre-filter is untouched** and remains a lossless superset
  (`test_lossless_*`): this change only tightens *scoring*, never retrieval.

### 5. Recall risk

- **Honest uploaders who retitle scenes** (the `{site, date}` recall concern from
  PR #20) carry over unchanged: a genuine release whose title is reworded but
  still overlaps sits at ratio ≥ 60, well above 40, and corroborates.
- **New surface from generalization — `{site, performer}` and `{site, date,
  performer}`.** A legitimate scene where studio + performer (+ date) all match
  but the tracker's title is *heavily* reworded (ratio < 40 **and** ≥ 3 distinct
  new tokens) would now be vetoed. This surface is narrow — reworded titles
  almost always retain some original words, which lifts the ratio — and the sole
  corpus `{site, performer}` case (Zarina Noir) sits at ratio 80, far clear. It
  is nonetheless a real recall surface and a natural **corpus watch-item**: the
  first legitimate high-attribute / low-title-similarity match that surfaces
  becomes a corpus `match` entry and, if the pattern recurs, the argument for a
  per-arm ratio or a residual-scaled gate.

### 6. Docstring (`scenehound/matcher.py`)

Rewrite the existing foreign-title bullet in the module docstring so it describes
the generalized arm. Replace "when site+date is the ENTIRE strong set" with "when
the strong set is title-less (any pair or triple of site/date/performer, with no
title match)", and keep the "absence is not contradiction" and "2 residual tokens
are routinely filler" sentences. Note the raised ratio and the reason (a generic
scene title fuzz-aligns above the old 35 gate).

### 7. UI trace copy (`scenehound/static/ui.html`)

The current `foreign-title` copy is now factually wrong for the new arms — it
says *"its title and performers don't match, and studio + date alone can't carry
it,"* but in the `{date, performer}` incident the performers **do** match and the
studio does **not**. Rewrite it pair-agnostic, e.g.:

```js
  "foreign-title": "Rejected: the release's title names a different scene — the signals that do agree (studio, date, or performer) can't identify it without a matching title.",
```

`observe.py` already records the full `detail` dict; this is a rendering-copy
change only, no Python change. The `date_secondary_reading` marker from PR #20 is
untouched.

### 8. Tests

`tests/test_matcher.py`:

- **Incident regression** (`{date, performer}`): the GloryholeSecrets scene and
  release → `veto == "foreign-title"`, `confidence < 75`, strong `("date",
  "performer")`.
- **`{date, performer}` absence still matches**: bare `Performer (YYYY-MM-DD)`
  with zero residual → no veto, `{"date", "performer"} <= strong`, confidence ≥ 75.
- **`{date, performer}` legit recall**: the Jane+Mary corpus construction (residual
  0, ratio 76.5) → no veto, confidence ≥ 75. Guards the sole in-corpus
  title-less date+performer match.
- **`{date, performer, title}` exemption**: near-exact title + performer + date →
  no veto, `"title" in strong`, confidence ≥ 75.
- **`{site, date, performer}` foreign now vetoes**: all three attributes agree,
  title foreign (≥ 3 residual, ratio < 40) → `veto == "foreign-title"`.
- **`{site, date, performer}` corroborating title still matches**: same triple,
  title fuzzy-overlaps (ratio ≥ 40) → no veto, confidence ≥ 75.
- **`{site, performer}` corroborating still matches** (Zarina Noir shape): ratio
  80 → no veto — pins that the generalization does not regress the existing
  site+performer path.
- **Ratio-constant regression**: a `{site, date}` candidate at ratio in `[35,
  40)` with ≥ 3 residual that matched before now vetoes — pins the 35 → 40 raise
  (optional if no natural fixture; the incident already exercises the > 35 band).

`tests/test_corpus.py` (parametrized — no code change): the appended entry below.

Pinned `rapidfuzz.fuzz.token_set_ratio` values (computed against the real library
in `.venv`, do not estimate):

- `"case no bending the right way"` vs the incident candidate content tokens =
  **36.4**; residual = 5 distinct (`first, glory, gloryholesecrets, hole, s`).
- `"latex worship session"` vs `"Jane Doe & Mary Major - Latex Worship
  (07.07.2026) 2160p"` = **76.5**; residual 0.
- `"big titty step sistinder match"` vs the Zarina Noir candidate = **80.0**;
  residual 3.
- Synthetic foreign `{date, performer}` unit candidate `"Jane Doe - Completely
  Different Bondage Clip (2026-07-07) 1080p"` vs `"latex worship session"` =
  **25.4**; residual 4.

### 9. Corpus (`tests/fixtures/corpus.yaml`)

Append the production mismatch as `expect: no_match` (it scores 75 and matches
today; the fix kills it):

```yaml
# --- 2026-07-15 production false grab #3: {date, performer} with a contradicting title ---
# Sydney Paige really is in both scenes and the primary yyyy-mm-dd reading (June 14) is
# genuinely 1 day from the wanted June 15 — date (+40) and performer (+35) are both honest,
# so Layer A does not apply. But the release plainly names a different scene AND a different
# studio (GloryholeSecrets != Shoplyfter Mylf). The {site,date}-only foreign-title veto did
# not arm for {date,performer}; the generalized title-less arm kills it (5 distinct residual
# tokens, ratio 36.4 < 40).
- release: "[GloryholeSecrets] Sydney Paige's First Glory Hole - Sydney Paige (2024-06-14) [2160p]"
  scene:
    site: "Shoplyfter Mylf"
    aliases: ["Shoplyfter"]
    date: 2024-06-15
    title: "Case No. 8002506 Bending the Right Way"
    performers: ["Sydney Paige"]
  expect: no_match
```

(The `aliases` should reflect the real wanted fingerprint; none may match the
foreign studio. The candidate does not name *Shoplyfter Mylf* at all, so `site`
is not a strong signal and the strong set is `{date, performer}` — the entry
reproduces the false grab.)

## Global Constraints (carried from PR #20)

- `scenehound/matcher.py` and `scenehound/dates.py` stay **pure** — no I/O, no
  config reads; tunables are module-level constants (no new config entries).
- `tests/fixtures/corpus.yaml` is the accuracy ratchet: every existing entry must
  keep passing; the GloryholeSecrets incident is appended as `no_match`.
- The wanted-index pre-filter must remain a lossless superset (`test_lossless_*`).
- Any `rapidfuzz` `token_set_ratio` value a test depends on is computed against
  the real library in `.venv` and pinned here — never estimated.
- Run the **full** suite (`.venv/bin/python -m pytest`) before every commit.
- Conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`); commit
  messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Out of scope

- **Version bump / release tagging.** PR #20, this work, and the multi-grab RSS
  badge follow-up ship as **one grouped release** — tag only when all three are
  merged.
- **Multi-grab RSS badge slot** (`observe.py record_grab` single `grabbed_guid`
  overwrite) — separate follow-up.
- **Studio-contradiction as its own signal** — residual mass already catches the
  foreign studio; revisit only with corpus evidence.
- **`query_planner.py` / `parse_query_term`** — separate, already-ordered path.
- **`import_completer.py`** — gates purely on confidence; nothing changes.
- **Accent transliteration in `normalize.py`** — known limitation, tracked
  separately.
- **`scenehound/dates.py`** — reading ranks shipped in PR #20; this incident's
  date is a genuine primary-reading match, so no date-layer change.
