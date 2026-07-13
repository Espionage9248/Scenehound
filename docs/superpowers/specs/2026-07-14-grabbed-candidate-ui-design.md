# Grabbed-candidate marker in the UI

**Date:** 2026-07-14
**Status:** Approved for planning

## Problem

When Whisparr grabs a release that Scenehound matched, the session badge and
outcome ladder show that *something* was grabbed and imported, but nothing in
the candidates table indicates *which* candidate it was. With many matched
candidates the user cannot easily tell which row was actually taken.

Real-world trace (2026-07-14): query `momcomesfirst 01.09.2020` produced 25
candidates, 11 matched at confidence 100. The grabbed release —
`[Brianna Beach] Mom Comes First 5/27/18 [1080p]` — sat third in the list with
no marker. The outcome ladder shows only the *rewritten* title
(`Mom.Comes.First.2020-09-01.Mom.Comes.First.XXX.1080p`), which is hard to map
back to a row because the table leads with original release titles.

Root cause: `SessionStore.record_grab` (`scenehound/observe.py`) already finds
the matching candidate by title when correlating the Grab webhook to a
session, but it only stamps the session-level `outcome.grab` and discards the
candidate identity.

Wrinkle the same trace exposes: two candidates in one session can carry an
*identical* `rewritten_title` (the top two both rewrote to
`Mom.Comes.First.2020-09-01.Mom.Comes.First.XXX`). The webhook reports the
rewritten title Whisparr saw, so title-only correlation is ambiguous in
general. The webhook payload carries `release.size` (currently ignored), which
is exactly what differs between duplicate-titled candidates.

## Decision

- **Correlation:** exact title match first; break ties with `release.size`
  from the Grab webhook ("size tie-break"), chosen over marking all title
  matches (noisy) and first-match-wins (occasionally badges the wrong twin).
- **Presentation:** badge the grabbed candidate's row **in place** — the list
  stays a faithful confidence-sorted record — chosen over pinning the row to
  the top (rewrites history) and over an anchor-link from the outcome ladder
  (more JS than this deliberately tiny UI wants).
- **Unresolvable ambiguity degrades to current behaviour:** session-level
  grab is stamped, no row is badged.

## Design

### 1. Data model (`scenehound/observe.py`)

- `GrabEvent` gains `size: int | None` — the webhook's `release.size`.
- `Outcome` gains `grabbed_guid: str | None = None` — the sanitized
  `CandidateTrace.guid` of the grabbed candidate. `guid` is already stored
  per-candidate as identity-only (never displayed), so it is the natural
  stable key. It lives on `Outcome` because that is the deliberately-mutable
  part of an otherwise frozen session, and it is stamped at the same moment
  as `outcome.grab`, keeping the existing mutability story intact.
- `record_grab(release_title, download_id, size)` correlation, per session
  (newest first, as today):
  1. Collect **all** candidates whose `rewritten_title` or `title` equals the
     grabbed title.
  2. Exactly one → stamp `outcome.grab` and `outcome.grabbed_guid`.
  3. Several → keep those whose `size` equals the webhook size exactly;
     exactly one survives → stamp both.
  4. Still ambiguous (webhook size `None`, or zero/multiple size matches) →
     stamp `outcome.grab` only; `grabbed_guid` stays `None`. The UI degrades
     to exactly today's behaviour.
- Second-grab overwrite (accepted v0.2.0 limitation) is retained; the
  overwrite restamps `grabbed_guid` together with `grab` (including back to
  `None` if the second grab is ambiguous), so the two can never drift apart.
- `record_import` is untouched: import correlation is keyed on `download_id`
  at session level, which is unambiguous.
- `UnmatchedGrab` is untouched: by definition it has no candidate to point at.

### 2. Webhook (`scenehound/import_api.py`)

Extract `release.size` defensively in the existing style: accept `int`
(coerce via `int(...)` guarded the same way the other fields are), anything
missing or junk becomes `None`. Pass it to `store.record_grab`.

### 3. UI (`scenehound/static/ui.html`)

- Candidate row where `c.guid === s.outcome.grabbed_guid` gets an in-place
  badge reusing the existing badge classes, mirroring session-badge
  precedence (`badge(s)` logic): `Imported` (`b-imported`) or
  `Would import (dry-run)` (`b-dryrun`) when `outcome.imported` is set, else
  `Grabbed` (`b-grabbed`).
- Outcome ladder: when `grabbed_guid` resolves to a candidate, the
  "Whisparr grabbed `<rewritten>`" line also shows the candidate's original
  release title, so the ladder and the candidates table speak the same
  language. When it doesn't resolve, the line renders as today.
- Guard on field presence (`s.outcome.grabbed_guid` may be absent in
  sessions recorded before the change; the store is an in-memory ring, so
  mixed-shape snapshots only exist within one process lifetime, but the
  guard is free).

### 4. Tests

`tests/test_observe.py`:

- Unique title match → `grabbed_guid` stamped alongside `grab`.
- Two candidates with identical `rewritten_title`, different sizes, webhook
  size matches the second → second candidate's guid stamped.
- Identical titles, webhook size `None` (or matching neither / both) →
  `grab` stamped, `grabbed_guid` is `None`.
- Second grab correlating to the same session restamps both fields; an
  ambiguous second grab resets `grabbed_guid` to `None`.
- Import after grab leaves `grabbed_guid` unchanged.
- Snapshot JSON includes `grabbed_guid` and `GrabEvent.size`.

`tests/test_import_api.py`:

- `release.size` parsed and forwarded; missing, non-numeric, and absent
  `release` dict all degrade to `None` without erroring.

`tests/test_ui_api.py`:

- Snapshot shape: `outcome.grabbed_guid` present in serialized sessions.

### Out of scope

- Persisting grab correlation across restarts (the store is an in-memory
  ring by design).
- Fixing the second-grab-overwrite limitation itself.
- Any change to matching, rewriting, or the import completer.
