# Import-Completer — Design Spec

**Date:** 2026-07-12
**Status:** Approved for implementation (TDD pass on branch `feat/import-completer`)

## Problem

Some scenes Whisparr v3 (eros) grabs via Scenehound download fine but never
auto-import. Whisparr holds them in the queue with:

> "Found matching movie via grab history, but release was matched to movie by
> ID. Manual Import required."

Root cause (proven): Whisparr identifies a scene by studio ForeignID +
ReleaseDate + fuzzy title. At **grab** time it parses Scenehound's rewritten
title and matches perfectly. At **import** time it re-parses the downloaded
**file name** — the tracker's original, baked into the .torrent (e.g.
`AdultTime_MaybeTheyAREALittleBigger_2160p_h265.mp4`: network-level studio, no
date) — cannot scene-match it, falls back to the grab-history movie ID, and its
safety rule holds by-ID matches for manual confirmation.

The file cannot be renamed (breaks the .torrent infohash / seeding), and
Whisparr already hardlinks a clean library name on import. The only missing
piece is auto-triggering the held import.

## Constraints (decided, not re-litigable)

- Built **into Scenehound** as a bounded, opt-in subsystem: feature-flagged
  **off by default**, **dry-run by default** when enabled. Fully isolated from
  the Torznab search path; a structural no-op when disabled.
- Webhook-driven from Whisparr's Connect → Webhook, "On Manual Interaction
  Required" event, to a new authenticated FastAPI route.
- Reuse `WhisparrClient` (extended with read/write ops), `WantedIndex`, and
  `matcher.score` for phase-2 matching.
- `importMode: "copy"` always — never `"move"` — so qBittorrent seeding
  survives.
- Phase 1: single-file by-ID auto-import behind a conservative safety gate.
  Phase 2: multipack/siterip matching. Independent flags; phase 1 ships first.

## Architecture: webhook as doorbell

The webhook is a **trigger, not a data source**. All authoritative state comes
from Whisparr's queue API. This makes webhook payload drift irrelevant, unifies
the webhook / reconcile / startup paths into one sweep function, and yields
natural idempotency (a successfully imported item leaves the queue).

### Components

- `scenehound/import_completer.py` — sweep decision logic + `ImportCompleter`
  service loop. New module; no changes to the search path.
- A small new router exposing `POST /import/webhook`.
- `create_app` wires the router **and** starts the completer task **only when
  `import_completer.enabled`**. Disabled means: no route registered (404), no
  background task, nothing constructed. The disabled guarantee is structural,
  not an `if` inside a handler.

### Webhook endpoint

- `POST /import/webhook?apikey=<scenehound api_key>` — reuses the existing
  Scenehound api_key as a query param, same pattern as the Torznab endpoints.
  Wrong/missing key → 401.
- Handles `eventType: "Test"` with 200 (required for the Connect to be
  saveable in Whisparr's UI).
- For any other event: log full payload at DEBUG, set the sweep event, return
  200 immediately. No slow work inside Whisparr's notification call.

### Sweep loop

One background task (peer of the existing `refresh_loop`):

1. Wait on `asyncio.Event` (rung by webhook) **or** timeout = min(reconcile
   interval, earliest pending grace expiry).
2. Run the sweep: `GET /api/v3/queue`, evaluate each item against the gate,
   act on eligible items.
3. One **startup sweep** shortly after app start covers every
   missed-webhook/downtime scenario — held items are still in the queue when
   Scenehound comes back. No persistence required.

### Idempotency / retry

All state is process-local (in-memory):

- **First-seen-held timestamps** per `downloadId` implement the grace period.
- **In-flight set** keyed by `downloadId` prevents overlapping action if a
  webhook rings mid-sweep.
- **Attempt counter** per `downloadId`: if a fired ManualImport does not clear
  the queue item, re-attempt on later sweeps up to `max_attempts` (default 3),
  then **park** the item — skip it with a single WARNING — until process
  restart.
- Natural dedup: imported items leave the queue, so duplicate doorbell rings
  find nothing to do.

## Phase 1 gate (single file, by-ID)

Every check fails safe: skip + structured log, item stays held for manual
import (exactly today's behavior). All checks must pass:

1. **Held state + message**: queue item `trackedDownloadState` ∈
   {`importBlocked`, `importPending`} AND a status message matching the by-ID
   pattern ("matched to movie by ID" / "Manual Import required"). Scopes
   phase 1 to exactly the proven failure mode; other hold reasons are skipped.
   Wording drift across versions fails safe (skip).
2. **movieId equality**: `GET /api/v3/manualimport?downloadId=<infohash>`
   returns a candidate whose `movie.id` equals the queue record's `movieId`.
   Non-negotiable core: we only confirm Whisparr's own grab decision, never
   introduce a new match.
3. **Single video file**: exactly one video-file candidate (samples/extras
   flagged by Whisparr excluded). Multi-file → phase 2 (or skip if phase 2
   disabled).
4. **Zero rejections**: candidate `rejections[]` must be empty. Any rejection
   → skip, with reasons logged prominently. Dry-run logs from the live
   instance are the evidence base for any future allowlist; none ships now.
5. **Grace period**: item must have been first seen held ≥ `grace_seconds`
   (default 120) ago. Avoids racing Whisparr's own completed-download handling
   or a still-settling qBittorrent.
6. **Verbatim metadata**: quality / languages / releaseGroup taken from the
   manualimport candidate unchanged. `importMode: "copy"`.

Action: `POST` the ManualImport command with the single file. In dry-run, log
the exact command body that would have been posted, and stop.

## Phase 2 (multipack / siterip) — behind `multipack` flag

Triggered when the single-file check fails and `multipack: true`.

- Per-file matching for candidates Whisparr did not pre-populate:
  `WantedIndex.candidates_for_title(filename)` → `matcher.score`.
- **Acceptance bar per file** (stricter than search — an import mistake lands
  a mislabeled file in the library and nothing re-verifies it):
  - confidence ≥ `import_threshold` (default 90), AND
  - `strong_signals` set, AND
  - **uniqueness margin**: best candidate beats runner-up by ≥
    `ambiguity_margin` (default 10) points, else ambiguous.
- Candidates Whisparr pre-populated with a movie count as matched only if that
  movie equals the grabbed `movieId`.
- **All-or-nothing pack gate (default)**: fire ManualImport only if **every**
  video-file candidate is accounted for (matched at the bar, or valid
  pre-population). Any unmatched/ambiguous file → **skip the entire pack** and
  log a per-file verdict table (matched → scene id + confidence; unmatched;
  ambiguous → top-2 candidates with scores). Rationale: partially importing a
  download-associated pack marks the download imported; when seed goals are
  met, remove-completed cleanup deletes the never-imported files — an
  unmatched file may be a wanted scene the matcher failed on, silently
  converted into permanent loss. The verdict log turns the manual fallback
  into a checkbox exercise.
- **Belt-and-braces re-check** before the POST: `GET /api/v3/movie/{id}` for
  each matched scene; require `monitored && !hasFile` (guards WantedIndex
  staleness, ≤15 min).
- Accepted files go in **one batched** ManualImport POST.
- Monitored-only is otherwise structural: `WantedIndex` is built from
  `/wanted/missing` (monitored + missing by definition).
- **Studio derivation is deferred**: pack filenames often carry a network name
  and no date, so the site signal may miss. v1 relies on title + performer
  signals; pack context (folder name, grabbed movie's studio) is logged as a
  diagnostic only. If dry-run shows real packs failing solely on the site
  signal, add the grabbed movie's studio as a scoring hint later — from
  evidence.

Accepted consequence: a siterip grabbed for one wanted scene usually will
**not** auto-complete under all-or-nothing. Phase 1 remains the workhorse;
dry-run verdict logs quantify what phase 2 leaves on the table.

### Partial import via folder-path (opt-in, probe-gated — NOT in initial scope)

`partial_import: true` (future flag, ships only after explicit live testing):
import the matched subset via **folder-path manualimport without a
`downloadId` association**, leaving the tracked download untouched. Expected
behavior: queue item stays held; `filterExistingFiles` hides the imported
subset on the next manual-import view, presenting only leftovers to the user;
remove-completed cleanup never fires because the download is never marked
imported. Three assumptions that MUST each be verified live before any
implementation:

1. Folder-path import does not transition the tracked download's state.
2. `filterExistingFiles` hides the already-imported subset in the queue
   item's manual-import view.
3. **Downstream import events still fire** — Connect notifications /
   media-server rescans triggered by import must demonstrably occur for
   folder-path imports, or media won't be picked up and the feature is worse
   than useless.

If any assumption fails, the behavior stays all-or-nothing. Sweep logic must
treat "partially imported, leftovers held" as parked-by-design, not a failed
import to retry.

## Configuration

```yaml
import_completer:
  enabled: false          # master flag — subsystem not wired at all when false
  dry_run: true           # full pipeline, logs the ManualImport it WOULD fire
  multipack: false        # phase-2 flag, independent of phase 1
  grace_seconds: 120
  reconcile_seconds: 900
  max_attempts: 3
  import_threshold: 90
  ambiguity_margin: 10
```

Env overrides in house style: `SCENEHOUND_IMPORT_ENABLED`,
`SCENEHOUND_IMPORT_DRY_RUN`, `SCENEHOUND_IMPORT_MULTIPACK`,
`SCENEHOUND_IMPORT_GRACE`, `SCENEHOUND_IMPORT_RECONCILE`,
`SCENEHOUND_IMPORT_MAX_ATTEMPTS`, `SCENEHOUND_IMPORT_THRESHOLD`,
`SCENEHOUND_IMPORT_MARGIN`. Frozen dataclass `ImportCompleterConfig` on
`Config`, defaults preserved when the YAML block is absent.

Rollout ladder: off → enabled + dry-run (observe logs) → live. Phase 2 repeats
the ladder under `multipack`.

## WhisparrClient extensions

- `fetch_queue()` — `GET /api/v3/queue` (paged if needed).
- `fetch_manual_import(download_id)` —
  `GET /api/v3/manualimport?downloadId=…&filterExistingFiles=…`.
- `fetch_movie(movie_id)` — `GET /api/v3/movie/{id}`.
- `post_manual_import(files, import_mode="copy")` — the ManualImport command
  POST (exact endpoint/body confirmed by probe 4 below).

`SceneFingerprint.scene_id` is set from the wanted-record `id`, which is
assumed to be the movie id ManualImport needs — **verify early** (probe 3).

## Probes (step one of implementation)

Deliverable: a small curl script/checklist run against the live instance
(`http://192.168.1.5:6979`) while held items exist; sanitized captures become
test fixtures.

1. `GET /api/v3/queue` → held-item shape: `trackedDownloadState` value,
   `statusMessages` wording, `movieId`, `downloadId`.
2. `GET /api/v3/manualimport?downloadId=<infohash>` → candidate shape: is
   `movie` pre-populated, contents of `rejections[]`, quality/languages
   fields, sample/extra flagging.
3. Confirm `scene_id == movieId`: a wanted-record `id` equals the id the queue
   record's `movieId` refers to.
4. ManualImport POST body + command name: source-read of the Whisparr eros
   branch; validated live via the dry-run → live rollout (no safe write
   probe). Also confirm the exact webhook event name for the Connect config;
   the payload itself is only logged at DEBUG.

## Testing

- **Sweep as a decision function**: (queue records, manualimport candidates,
  config, clock) → decisions (import / skip+reason / park). Tested against
  captured fixtures `tests/fixtures/whisparr_queue_sample.json` and
  `whisparr_manualimport_sample.json` (same convention as
  `whisparr_wanted_sample.json`). No HTTP in decision tests.
- **Client tests**: mocked-transport tests for the new WhisparrClient ops,
  matching the existing client-test style.
- **Corpus tests for phase 2**: new corpus section of real pack filenames →
  expected scene / expected-skip, run through `candidates_for_title` +
  `score` + the import bar. Threshold/margin tuned there, not by vibes.
- **Disabled/dry-run guarantees — three explicit tests**:
  1. Default config parses to `enabled=False, dry_run=True`.
  2. `create_app` with defaults registers no `/import/*` route (404) and
     starts no completer task.
  3. With `enabled=True, dry_run=True`, a full sweep against fake transports
     performs **zero non-GET calls** — asserted by a transport spy that
     whitelists GETs. Dry-run is a hard property, not a convention.
- Full suite via `.venv/bin/python3.12 -m pytest -q`; strict TDD throughout.

## Out of scope

- Renaming downloaded files (breaks seeding).
- Any change to the Torznab search path.
- Persistent state (DB/files) for the completer.
- Studio-derivation pipeline for pack files (deferred pending dry-run
  evidence).
- Rejection allowlist (deferred pending dry-run evidence).
- `partial_import` implementation (probe-gated future opt-in, see above).
