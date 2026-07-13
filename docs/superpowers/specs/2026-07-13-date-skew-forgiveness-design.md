# Date-skew forgiveness

**Date:** 2026-07-13
**Status:** Approved for planning

## Problem

The matcher treats any date parsed from a release title as authoritative: if it
is more than ±1 day from the wanted scene's date, `score()` returns the
`date-mismatch` veto and the release is rejected regardless of all other
evidence (`scenehound/matcher.py`, date block).

Real-world failure (2026-07-13 trace): wanted scene *Household Fantasy — Big
Titty Step-Sistinder Match ~ Zarina Noir (2026-07-07)*; candidate release
`[ScottStark-HouseholdFantasy] Zarina Noir - Big Titty Step Sister Tinder Match
(2026-07-05) [1080p]`. The release carried three corroborating signals — site
(alias hit), performer, near-exact title — and would have scored ~90 (site 35 +
performer 35 + fuzzy title ~20, two strong signals, no cap). The uploader had
stamped their own date, 2 days off the scene's date, and the veto zeroed
everything.

Root cause class: uploaders stamping rip/upload dates instead of the studio's
release date. These skews are typically small (a few days).

Perversity worth naming: had the uploader omitted the date entirely, the
release would have matched at ~90. A slightly-wrong date currently makes a
release *less* matchable than no date at all — correct when the date is
load-bearing evidence, wrong when the rest of the evidence independently
clears the two-strong-signal rule.

## Decision

Approach C ("hybrid") from the brainstorm, chosen over:

- **A. Widen the soft window only** — forgives skewed dates even for weak
  matches; does nothing for the sibling defence trade-off analysis.
- **B. Evidence-override at any distance** — fully reopens the series-sibling
  hole ("Part 3" months later, same site/performer/similar title).

**C:** a mismatched date is forgiven only when **both** hold:

1. the skew is small — within a configurable window; and
2. the match clears the two-strong-signal rule *without* the date.

Rationale: uploader skew is a few days; series siblings with near-identical
titles are almost never 2–3 days apart. Outside the window, or with weak
remaining evidence, the date remains a hard contradiction.

## Design

### 1. Matcher rule (`scenehound/matcher.py`)

`score()` currently early-returns the `date-mismatch` veto inside the date
block, before site/performer/title are computed. Restructure: the date block
records the outcome (`hit`, or pending mismatch with
`off = min(abs(d - scene.date).days for d in title_dates)`), and the veto
decision is made after the other signal blocks have populated `strong`:

- `off <= 1` → strong date hit, exactly as today (`STRONG_DATE` points).
- `2 <= off <= date_skew_days` **and** ≥2 non-date strong signals →
  **forgiven**: no veto, date contributes 0 points,
  `detail["date_skew_days"] = float(off)` records the skew for the trace.
- Otherwise → `MatchScore(0, (), "date-mismatch", ...)` as today.

Notes:

- The two-strong-signal cap (`SINGLE_SIGNAL_CAP`) needs no change: a forgiven
  match has ≥2 strong signals by construction.
- The site-mismatch veto keeps its current relative behaviour; only the date
  veto's timing moves. When both a date mismatch and a site mismatch apply,
  the reported veto may be either — both are hard rejections and the choice
  is not load-bearing.
- Multiple dates in a title: `off` is the minimum distance across all parsed
  dates (any date within ±1 is already a hit, as today).
- The title-strong rule ("title never pairs with date alone") is unaffected:
  it keys off site/performer, not date.

### 2. Config (`scenehound/config.py`)

New `matching.date_skew_days`: int, **default 3**. Env override
`SCENEHOUND_DATE_SKEW_DAYS`, yaml `matching.date_skew_days`, same plumbing
pattern as `matching.threshold`. Setting it to 1 (or 0) makes the forgiveness
band `2..skew` empty and restores the old hard-veto behaviour.

`score()` gains the window as a parameter (defaulting to 3 to keep the
function pure and tests simple); callers pass the configured value.

### 3. Auto-import (`scenehound/import_completer.py`)

No change. The completer gates purely on
`confidence >= import_threshold`; a forgiven match that scores ≥90 is
auto-import eligible. This is deliberate (decided 2026-07-13): forgiven
matches are not second-class.

### 4. UI trace (`scenehound/static/ui.html`, `scenehound/observe.py`)

- Update the hard-veto copy (currently "…isn't the scene's date (±1 day)") to
  reflect the new rule, e.g. "…too far from the scene's date, or too weak to
  forgive the skew".
- Accepted matches whose detail carries `date_skew_days` render a visible
  marker, e.g. *"date 2 days off — forgiven: 2 other strong signals"*, so
  forgiveness firings are auditable.
- `observe.py` already records the full `detail` dict; this is a rendering
  change plus whatever trivial plumbing the marker needs.

### 5. Tests

Matcher (`tests/test_matcher.py` or equivalent):

- Forgiven: site + performer present, date 2 days off → no veto, ~90,
  `strong == ("site", "performer")`, `detail["date_skew_days"] == 2.0`.
- Veto retained beyond window: same signals, date `date_skew_days + 1` off →
  `date-mismatch`.
- Veto retained on weak evidence: site only (one non-date strong signal),
  date 2 days off → `date-mismatch`.
- Boundary: skew exactly `date_skew_days` with 2 strong signals → forgiven.
- Old behaviour restorable: `date_skew_days=1` → 2-days-off vetoes even with
  site + performer.
- Date hit unchanged: ±1 day still scores `STRONG_DATE` and appears in
  `strong`.

Config: `date_skew_days` from env and yaml, default 3.

### Out of scope

- `wanted_index.py` — its ±1 windows resolve *Whisparr's query* to a wanted
  scene and pre-filter RSS candidates; forgivable matches always carry a site
  or performer token, so the token index already keeps them as candidates.
- Query planner, importer all-or-nothing logic, normalize layer.
