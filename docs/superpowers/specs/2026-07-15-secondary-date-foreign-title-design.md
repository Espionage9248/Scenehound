# Secondary-reading date demotion + foreign-title veto

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

The matcher grabbed and imported the wrong release on 2026-07-15. Wanted scene:
*Family Therapy XXX — Slut Training Day (2014-07-25)*. Candidate release:
`[FamilyTherapy] Alexa Chains - The Goth Latina Experience [26-07-14] [1080p]`
— an entirely different scene, actually dated 2026-07-14 in the uploader's
`yy-mm-dd` convention. Matched at exactly threshold (75 = date 40 + site 35)
and Whisparr grabbed it.

Two independent failures:

1. **Date-reading cherry-pick.** `extract_dates` (`scenehound/dates.py`)
   expands every 2-digit triple into all three readings and returns them as
   one flat set: `26-07-14` yields 2026-07-14 (`yy.mm.dd`, the dominant scene
   convention — correct here) *and* 2014-07-26 (`dd.mm.yy`). The matcher takes
   `min(days-off)` across all readings — the friendliest possible
   interpretation. 2014-07-26 is 1 day from the scene's 2014-07-25, inside the
   ±1 tolerance, so a strong date signal was fabricated from the *least*
   plausible reading while the dominant reading was 12 years off.

2. **Contradiction blindness.** The candidate plainly named a different scene
   — distinctive title ("The Goth Latina Experience"), foreign performer
   ("Alexa Chains") — with zero overlap against "Slut Training Day" and its
   performers. The scoring model has contradiction vetoes for site and date
   but nothing for title/performer: a candidate that names a *different* scene
   scores the same as one that names none.

Meta-context, not fixable by scoring: the true scene wasn't yet on StashDB, so
Whisparr's wanted list did not contain the right answer. The correct outcome
was "no match"; stricter negative evidence makes that the result instead of a
false grab.

## Decision

Two layers, both from the brainstorm, chosen over:

- **Raise threshold / lower site+date points** — rejected: site+date is a
  deliberately valid strong pair and bare `Site.YY.MM.DD` releases are
  legitimate; this punishes the honest case to catch the dishonest one.
- **Suppress secondary readings entirely** — rejected: hard-vetoes genuine
  `dd.mm.yy` uploaders (their primary reading contradicts even when the
  release is right).

**Layer A — reading-rank discipline:** only a *primary-reading* date match can
be a strong signal. A secondary-only match contributes zero points but still
forgives the date-mismatch veto (mirrors the date-skew-forgiveness pattern:
forgiven, traced, worth nothing).

**Layer B — foreign-title veto:** when the strong pair is exactly
{site, date} and the candidate carries substantial content tokens that overlap
~zero with the scene's title and performers, hard-veto. Distinguishes
*contradiction* (names a different scene) from *absence* (bare release name),
which keeps legitimate site+date-only matches alive.

On the incident both layers independently kill the match: A demotes the date
(one strong signal left, capped at 65 < 75); B covers the adjacent class where
the date collides via a *primary* reading (or a real same-day sibling) but the
candidate names a different scene.

## Design

### 1. Reading ranks (`scenehound/dates.py`)

`extract_dates` returns ranked readings instead of one flat set — a small
frozen dataclass:

```python
@dataclass(frozen=True)
class ExtractedDates:
    primary: frozenset[date]
    secondary: frozenset[date]   # disjoint from primary; primary wins overlap

    @property
    def all(self) -> frozenset[date]: ...  # union, for pre-filter use
```

Rank per pattern:

- `_TRIPLE2` (`26-07-14`): `yy.mm.dd` → **primary** (dominant scene
  convention); `dd.mm.yy`, `mm.dd.yy` → secondary.
- `_YMD4` (`2026-07-14`): `yyyy.mm.dd` → primary; `yyyy.dd.mm` → secondary.
- `_XY4` (`14.07.2026`): `dd.mm.yyyy` → primary (consistent with
  `parse_query_term`, whose dd.mm-first ordering is confirmed from live
  logs); `mm.dd.yyyy` → secondary.

A date reachable by a primary reading of any token is primary, even if a
secondary reading of another token also produces it (dedup: secondary −=
primary).

Callers:

- `matcher.score()` distinguishes ranks (below).
- `wanted_index.candidates_for_title` uses `.all` — the pre-filter must stay a
  lossless superset of anything the matcher could accept.
- `parse_query_term` is unchanged (already ordered, separate code path).

### 2. Layer A — matcher date block (`scenehound/matcher.py`)

The date block becomes, in order:

- primary reading within ±1 day → strong `date` hit, `STRONG_DATE` points —
  exactly as today.
- else secondary reading within ±1 day → **not strong, zero points**, no veto;
  `detail["date_secondary_reading"] = 1.0` records it for the trace.
- else → existing mismatch path: `off = min(days-off)` across **all** readings
  (leniency is safe here — skew forgiveness already requires two non-date
  strong signals and grants no points), then the existing skew-forgiveness /
  hard-veto decision, unchanged.

Recall check, honest `dd.mm.yy` uploader (`[Site] Performer - Title
[14-07-26]`, scene dated 2026-07-14): primary reading 2014-07-26 contradicts,
secondary 2026-07-14 matches → no veto, date not strong, matches on
site+performer(+title). The only class that dies is *secondary-reading date +
site with nothing else* — the incident class.

### 3. Layer B — foreign-title veto (`scenehound/matcher.py`)

Evaluated after all signal blocks, alongside the date-veto decision. Applies
**only when** `set(strong) == {"site", "date"}` — any pair including performer
or title already has content confirmation. Then veto `"foreign-title"` when
all of:

1. the scene title is distinctive: `len(content_tokens(scene.title)) >=
   _MIN_TITLE_STRONG_TOKENS` (reuses the existing constant, 2);
2. the candidate's **residual tokens** number ≥ `_MIN_FOREIGN_RESIDUAL`
   (**3**, not the 2 originally drafted: the corpus's legitimate
   `FamilyTherapyXXX.26.07.07.Bonus.Scene.XXX.1080p` entry — the xxx-suffix
   toggle case where site+date is deliberately load-bearing — carries exactly
   2 filler residual tokens at ratio 34.8; two leftover tokens are routinely
   filler, three-plus is evidence of a different title). Residual =
   `content_tokens(candidate)` minus tokens *and squashed forms* of the
   scene's site and every alias (glued `[FamilyTherapy]` must not count as
   foreign), minus `content_tokens(scene.title)`, minus tokens and squashed
   forms of every scene performer name (a partial performer presence — first
   name only — is neither a hit nor foreign evidence);
3. the fuzzy title ratio is below `_FOREIGN_TITLE_RATIO` (35) — computed in
   the title block regardless of the existing `_TITLE_RATIO_GATE`; no
   performer hit is implied by the strong-pair condition.

Constants are module-level, not config — no evidence yet that they need
tuning per deployment (the date-skew knob earned its config entry from a
corpus argument; these can follow if needed).

Behaviour table:

| Candidate | Residual | Outcome |
|---|---|---|
| `FamilyTherapy.14.07.25.XXX.1080p` | ∅ | site+date match, as today |
| `[FamilyTherapy] Alexa Chains - The Goth Latina Experience [26-07-14]` | `alexa chains the goth latina experience` (6) | veto `foreign-title` |
| Release with fuzzy title ≥ 35 ratio | any | no veto (title corroborates) |

### 4. Docstring

The `matcher.py` module docstring documents the false-positive defenses;
extend it with both new rules (secondary-reading demotion, foreign-title
veto) in the same style as the existing bullets.

### 5. UI trace (`scenehound/static/ui.html`, `scenehound/observe.py`)

- New veto copy: `"foreign-title": "Rejected: the release names a different
  scene (title/performers don't match) and only studio + date agree."`
- Accepted/scored candidates whose detail carries `date_secondary_reading`
  render a marker, e.g. *"date matched only via an alternate reading of an
  ambiguous format — not counted as strong"*, in the same spot the
  `date_skew_days` marker renders.
- `observe.py` already records the full `detail` dict; rendering change only.

### 6. Tests

`tests/test_dates.py`:

- `26-07-14`: 2026-07-14 in primary, 2014-07-26 in secondary.
- `2026-07-14`: primary; the `yyyy.dd.mm` alternate (when valid) secondary.
- `12-07-2026`: 2026-07-12 primary, 2026-12-07 secondary.
- Primary-wins dedup: a date produced by a primary reading of one token and a
  secondary reading of another appears only in primary.
- `.all` equals the union (pre-filter contract).

`tests/test_matcher.py`:

- **Incident regression**: FamilyTherapy scene, date 2014-07-25, title "Slut
  Training Day"; candidate `[FamilyTherapy] Alexa Chains - The Goth Latina
  Experience [26-07-14] [1080p]` → no 75; strong == ("site",) at most,
  confidence ≤ 65 (and if B evaluates first, veto `foreign-title` — either
  rejection is acceptable, assert not-matched rather than a specific path).
- Secondary-reading recall: site + performer + secondary date → match, no
  veto, `"date" not in strong`, `detail["date_secondary_reading"] == 1.0`.
- Primary date unchanged: ±1 primary → strong, `STRONG_DATE`.
- Secondary + site only → confidence capped at 65, no strong date.
- Foreign-title veto: site + primary-reading date, distinctive scene title,
  candidate names a different title with ≥3 residual tokens, ratio < 35 →
  veto `foreign-title`.
- Threshold guard: 2 residual filler tokens (`Bonus.Scene`, the xxx-suffix
  corpus case) → no veto, still matches on site+date.
- Absence still matches: `Site.YY.MM.DD.XXX.1080p` (zero residual) → site+date
  clears as today.
- No veto when fuzzy title corroborates (ratio ≥ 35) or performer hits.
- Generic scene title (1 content token) → B never fires.

`tests/test_wanted_index.py` (or equivalent): pre-filter still finds a scene
whose date is reachable only via a secondary reading.

### Out of scope

- `query_planner.py` / `parse_query_term` — separate, already-ordered path.
- `import_completer.py` — gates purely on confidence; nothing changes.
- Accent transliteration in `normalize.py` (known limitation, tracked
  separately).
- Performer-roster contradiction (veto when the candidate names a performer
  known from *other* wanted scenes) — unreliable here since the true scene
  wasn't in the wanted list at all; revisit only with corpus evidence.
