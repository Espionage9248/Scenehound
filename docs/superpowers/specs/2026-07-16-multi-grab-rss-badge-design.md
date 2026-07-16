# Same-session multi-grab RSS badge

**Date:** 2026-07-16
**Status:** Approved for planning

## Problem

`Outcome` carries a single grab slot. When two candidates in **one** RSS
session are both grabbed, the second Grab webhook overwrites the first
(`SessionStore.record_grab`, `scenehound/observe.py`, accepted-limitation
comment at ~171–174). Only the last grabbed candidate keeps a badge; the
earlier grab's import then surfaces via `unmatched_grabs` instead of on its own
row. This is a documented v0.2.0 accepted limitation (explicitly deferred in
the 2026-07-14 grabbed-candidate spec's "Out of scope") that has now been
bitten live: an RSS sync returns many rewritten candidates, Whisparr grabs more
than one, and the UI can only account for the last.

Root cause: `Outcome` holds `grab`, `grabbed_guid`, and `imported` as three
single-value slots. Two independent correlations run over them:

- **grab → candidate**, by title (`rewritten_title`/`title`), tie-broken by the
  webhook's `release.size` → sets `grabbed_guid`.
- **import → grab**, by `download_id` (each grab carries its own).

Because each grab has a distinct `download_id`, each can also import
independently — so multi-grab implies multi-import. Both correlations need to
be per-grab, not per-session.

Relevant fact: `SessionStore` is a purely in-memory bounded `deque`, rebuilt
empty on every process start — there is **no disk persistence**. So the
"replay of pre-feature sessions" concern is only about the UI's `render()` not
throwing on a shifted field shape within a single process lifetime, not about
migrating stored data.

## Decision

- **Data model:** replace the three single-value slots with a list of bundled
  records. `Outcome.grabs: list[GrabRecord]`, where each `GrabRecord` bundles
  `{grab, grabbed_guid, imported}`. Chosen over parallel `grabs`/`imports`
  lists correlated in JavaScript (pushes correlation into the UI, against
  observe.py's "Python owns correlation, UI only renders" design) and over
  keeping the single slots alongside a list (two sources of truth that can
  drift; unnecessary given the store is in-memory only).
- **Correlation logic is preserved exactly, per record.** The existing
  guid tie-break (single title match → its guid; else size disambiguation;
  else `None`) is factored into a helper and reused for each grab, so every
  current tie/ambiguity outcome holds per record.
- **Idempotent re-grab:** a grab whose non-empty `download_id` already has a
  record in the session updates that record in place instead of appending a
  duplicate (webhook resend / re-grab). An empty `download_id` always appends.
- **`unmatched_grabs` keeps its role unchanged** — events that correlate to no
  session at all (UI enabled mid-flight, title matches nothing). It is
  inherently one-per-`download_id`, so `UnmatchedGrab` is untouched.
- **Presentation:** badge every grabbed candidate row (each showing *its own*
  grab's state); one counted summary pill per present state; one outcome-ladder
  entry per grab, each independently upgradable to imported. A single-grab
  session renders pixel-identical to today.

## Design

### 1. Data model (`scenehound/observe.py`)

- **New `GrabRecord`** (mutable, like `Outcome`):

  ```python
  @dataclass
  class GrabRecord:
      grab: GrabEvent
      grabbed_guid: str | None = None
      imported: ImportEvent | None = None
  ```

- **`Outcome`**: remove `grab`, `grabbed_guid`, `imported`; add
  `grabs: list[GrabRecord] = field(default_factory=list)` (requires
  `from dataclasses import field`). `commit()` is unchanged — `grabs` defaults
  empty.

- **`_correlate_guid(matches, size)` helper** (staticmethod) — the exact
  current tie-break, extracted verbatim:
  1. `len(matches) == 1` → `matches[0].guid`.
  2. `size is not None` and exactly one candidate has that `size` → its guid.
  3. otherwise `None`.

- **`record_grab(release_title, download_id, size=None)`** — find the newest
  session with title matches (unchanged), then:
  - compute `guid = _correlate_guid(matches, size)`;
  - if `download_id` is non-empty and a record with that `download_id` already
    exists in `s.outcome.grabs`, update its `grab` and `grabbed_guid` in place
    (leave its `imported`);
  - otherwise append a new `GrabRecord(grab=ev, grabbed_guid=guid)`.
  - No title-matching session → `unmatched_grabs` (unchanged).
  - (Dedupe is scoped to the matched session — the same "newest matching
    session" semantics as today.)

- **`record_import(download_id, movie_id, file_count, dry_run)`** — iterate
  each session's `s.outcome.grabs` and stamp `rec.imported` on the record whose
  `rec.grab.download_id == download_id` (guarded on truthy `download_id`). Then
  fall through to `unmatched_grabs`, then the "import for a grab we never saw"
  branch — both unchanged.

- **`snapshot()`** — no change; `dataclasses.asdict` recurses the `grabs` list
  into a list of dicts automatically.

### 2. UI (`scenehound/static/ui.html`)

- **`grabBadge(rec)`** — body unchanged (reads `.imported` / `.grab`); now
  receives a `GrabRecord` rather than the whole `Outcome`. Same class/label
  precedence: `Imported` (`b-imported`) / `Would import (dry-run)` (`b-dryrun`)
  / `Grabbed` (`b-grabbed`).
- **Session summary pill** (`badge` + `render`): aggregate across
  `o.grabs` into one counted pill per present state, ordered
  **Imported / Would import / Grabbed**, reusing the existing badge classes.
  The `×N` suffix is **omitted when N == 1**, so a single-grab session looks
  exactly like today. `badge(s)` returns an array of `[cls, label]` pills;
  `render()` joins them. Grabs still take precedence over Matched/RSS/etc.
- **Per-row badge**: build a `guid → record` map from `o.grabs` (records whose
  `grabbed_guid` is set); each candidate row whose `guid` is in the map shows
  `grabBadge(record)` for *its own* grab.
- **Outcome ladder**: iterate `o.grabs`; per record emit the "Whisparr grabbed
  `<title>`" line (naming the candidate's original title when it differs, as
  today), followed by that record's import line when `rec.imported` is set.
  The failure/matched/passthrough lines above are unchanged.
- All reads guard `(o.grabs || [])` — defensive against shape, free given the
  in-memory-only store.
- `unmatched_grabs` rendering is unchanged.

### 3. Tests

**`tests/test_observe.py`** (heaviest):

- Rewrite grab/import assertions from `outcome.grab` / `grabbed_guid` /
  `imported` to `outcome.grabs[i].{grab,grabbed_guid,imported}`.
- **Replace** the two tests that encode the old overwrite limitation —
  `test_record_grab_second_grab_restamps_guid` and
  `test_record_grab_ambiguous_second_grab_resets_guid` — with tests asserting a
  second grab **appends a second record** (both preserved), including the case
  where the second grab is ambiguous (its own `grabbed_guid` is `None` while
  the first record is untouched).
- New: two grabs in one session each import independently (correlated by their
  own `download_id`); idempotent re-grab (same non-empty `download_id` updates
  in place, no duplicate record); per-record ambiguous guid preserved.
- Snapshot test: `outcome.grabs == []` for a fresh session (replacing the
  `grabbed_guid is None` assertion).

**`tests/test_ui_api.py`**: update snapshot-shape assertions to the `grabs`
list.

**`tests/test_import_api.py`** and **`tests/test_import_completer.py`**: update
any assertions that read the old single-slot shape (verify exact assertions
when writing the plan).

### Out of scope

- Persisting grabs across restarts (the store is an in-memory ring by design).
- Any change to matching, rewriting, retrieval, or the import completer's own
  logic (only the shape it reads/writes via `record_import` is affected).
- `matcher.py` / `dates.py` — untouched; they stay pure.
