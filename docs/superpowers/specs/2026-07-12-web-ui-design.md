# Scenehound v0.2.0 — Web UI Design

**Date:** 2026-07-12
**Status:** Approved for planning
**Branch:** `feat/web-ui`

## Purpose

Scenehound's matching pipeline is legible today only through debug logs, which are
hard to parse per-search. v0.2.0 adds a simple, local, read-only web UI that makes
each search legible at a glance, in plain human language:

- **Query chain**: what Whisparr asked for → what Scenehound understood → the query
  variants it sent to Prowlarr.
- **Candidates**: the releases Prowlarr returned, with scores.
- **Matches**: which candidates matched a wanted scene, with a plain-language "why"
  translating the raw score/confidence/strong-signal/veto internals.
- **Outcome**, clearly: an Imported ⊃ Grabbed ⊃ Matched ⊃ Failure ladder.
  Failure = zero matched results returned AND no grab.

## Constraints

- The UI must be **fully isolated from and non-blocking to** the Torznab search
  path. An observability bug degrades to a missing/partial UI entry, never a broken
  search or grab.
- **No persistence**: process-local, bounded, pruned in-memory state, mirroring how
  the import-completer bounds its state.
- **No log parsing**: a structured hook the search path writes to.
- No build step, no new runtime dependencies, no CDN assets. Ships inside the
  existing image.
- Strict TDD. Runtime Python 3.14; local tests via `.venv/bin/python3.12 -m pytest -q`;
  CI matrix 3.12 + 3.14.

## Key factual finding (corrects an initial assumption)

**Grabs bypass Scenehound.** The `<enclosure>` URL from Prowlarr is re-emitted
verbatim (`torznab.py`, `models.py`) and points at Prowlarr's own download proxy
(`clients/prowlarr.py`) — deliberately, so grabs reuse Prowlarr's tracker auth.
Whisparr fetches the torrent directly from Prowlarr; Scenehound never sees the
download. Grab detection therefore comes from **Whisparr's Connect webhook**
(`eventType=Grab`), not from a download-URL round-trip.

## Decisions (resolved during brainstorming)

| Question | Decision |
|---|---|
| Live vs recent | Browsable bounded buffer of recent sessions; browser **polls** a JSON endpoint every ~3–5 s (no SSE, no websockets). |
| Data capture | Explicit **recorder** object threaded through the search path (approach A); null-object when disabled. |
| Grab detection | **Whisparr On-Grab webhook** through the existing `/import/webhook` route; correlate by release title, carry `downloadId`. |
| Import tracking | **Only Scenehound-fired imports** (import-completer `ManualImport` commands, incl. dry-run, correlated by `download_id`). Whisparr's regular automated imports are out of scope. |
| Tech stack | FastAPI serves one static self-contained HTML page; vanilla JS renders from the JSON endpoint. One rendering path, zero new Python deps. |
| Isolation & mounting | New isolated router `ui_api.py` (pattern: `import_api.py`), mounted when `ui.enabled`. |
| Feature flag | **On by default** (`ui.enabled: true`, `SCENEHOUND_UI_ENABLED=false` to disable). Read-only + key-guarded + bounded justifies default-on. |
| Auth | Existing Scenehound API key guards the **data** endpoint (`?apikey=` query param, same mechanism as Torznab/webhook routes). `GET /ui` serves the static shell unauthenticated — it contains no data and no secrets — so bare links (unraid WebUI button, bookmarks) work; the page prompts once for the key and remembers it (localStorage). |
| Ports & deployment | **No new port**: the UI rides the existing FastAPI app on 9797 (already `EXPOSE`d/mapped). Dockerfile unchanged; compose example + unraid template updated to point at `/ui` and document the UI env vars. |
| Bounds | Ring buffer of **50 sessions** (config), **200 candidates/session** cap (config), matched candidates always kept, drop counts recorded. |
| Redaction | URLs (link/enclosure — they embed the Prowlarr API key) are **never stored**; GUIDs sanitized of `apikey`-style params; config keys never serialized. |
| Capture scope | Full chain detail for q-bearing searches incl. passthrough fallbacks; RSS syncs stored as one-line summary sessions; Torznab `caps`/auth-failure requests not captured. |

## Architecture

```
Whisparr ──q──▶ torznab_endpoint ──▶ _search_mode / _rss_mode / _passthrough
                     │                        │ (recorder calls at existing log points)
                     │ creates Recorder       ▼
                     └──────────────▶ observe.Recorder ──commit──▶ observe.SessionStore
                                                                     ▲          ▲
Whisparr Connect ──Grab──▶ /import/webhook ── record_grab ──────────┘          │
ImportCompleter ── fires ManualImport ── record_import ────────────────────────┘
                                                                     │ snapshot()
Browser ◀──HTML── GET /ui          GET /ui/api/sessions ◀──JSON──────┘
        └────────────── polls every ~3–5 s ─────────────┘
```

Import direction: `api.py` → `observe.py`; `observe.py` imports nothing from
`api.py`, `import_completer.py`, or FastAPI. `ui_api.py` imports `observe` types
only via app state.

## Data model (`scenehound/observe.py`)

All frozen dataclasses except the store.

```
SearchSession                      # one Torznab request = one session
  session_id: int                  # monotonic counter, process-local
  started_at / finished_at: float  # wall clock (time.time()) for display
  slug: str                        # indexer slug
  kind: "search" | "passthrough" | "rss"
  raw_query: str                   # exactly what Whisparr sent (q param; "" for RSS)
  parsed: ParsedQuery | None       # site_token + dates; None = unparseable
  scenes: tuple[SceneRef, ...]     # resolved wanted scenes
  threshold: int                   # matching threshold AT CAPTURE TIME
  variants: tuple[VariantTrace, ...]
  candidates: tuple[CandidateTrace, ...]  # best-score-per-GUID, conf desc, capped
  dropped_candidates: int          # how many the cap cut
  outcome: Outcome
  fallback_reason: str | None      # "unparseable-query" | "scene-unresolved" | "no-index"
  notes: tuple[str, ...]           # time-budget expiry, rate-deferrals, error text

SceneRef      = scene_id, site, date, title, performers
VariantTrace  = query, fired: bool, result_count: int | None
CandidateTrace
  title: str                       # ORIGINAL release title from Prowlarr
  guid: str                        # sanitized; identity only, not displayed
  size, seeders: int | None
  scene_id: int                    # best-matching scene
  confidence: int
  strong_signals: tuple[str, ...]  # from MatchScore
  veto: str | None
  detail: dict[str, float]         # per-signal points — feeds the "why" text
  matched: bool                    # confidence >= threshold
  rewritten_title: str | None      # what we returned to Whisparr, if matched

Outcome
  status: "matched" | "empty" | "error" | "rss-summary"
                                   # passthrough sessions use "matched"/"empty" from
                                   # the verbatim result count; the UI badge shows
                                   # "Passthrough" from kind, not status
  matched_count: int
  items_total / rewritten: int     # RSS summary counts (0 for searches)
  grab: GrabEvent | None           # filled later by webhook correlation
  imported: ImportEvent | None     # filled later by completer

GrabEvent   = release_title, download_id, at
ImportEvent = at, movie_id, file_count, dry_run: bool
```

RSS sessions reuse `SearchSession` with `kind="rss"`, empty `variants`, and only
*rewritten* items stored as candidates, plus `items_total`/`rewritten` counts.

### SessionStore

- `deque(maxlen=max_sessions)` + monotonic id counter. All handlers run on one
  asyncio event loop and no store method awaits, so no locking is needed; a comment
  pins that assumption.
- `add(session)`, `record_grab(release_title, download_id, at)`,
  `record_import(download_id, movie_id, file_count, dry_run, at)`,
  `snapshot() -> dict` (plain dicts, JSON-ready, newest first).
- **No store or recorder method ever raises**: bodies wrapped in
  `try/except Exception: log.exception(...)`.
- `record_grab` scans sessions **newest-first** for a candidate whose
  `rewritten_title` or original `title` equals the grabbed release title (exact
  string match — the rewritten title Scenehound emitted is what Whisparr reports
  back). First hit gets `outcome.grab`. No hit → appended to a bounded (20)
  `unmatched_grabs` list surfaced in the UI, so the signal is never silently dropped.
- `record_import` finds the session whose `outcome.grab.download_id` matches and
  stamps `outcome.imported`; otherwise stamps the matching unmatched-grab entry.
- Success/Failure as displayed: Success = `matched_count > 0` or a correlated grab;
  Failure = zero matched and no grab. Display precedence: Imported ⊃ Grabbed ⊃
  Matched ⊃ Failure.

## Capture: Recorder and call sites

`observe.Recorder` is constructed per request by `torznab_endpoint` (via a factory
on app state; when `ui.enabled` is false the factory returns a shared
`NullRecorder`). It accumulates a mutable draft and commits a frozen
`SearchSession` exactly once, in a `finally`. Call sites sit at the points that
already log:

| Where (api.py) | Call |
|---|---|
| `torznab_endpoint` | create recorder; commit in `finally`; `ProwlarrError` → `rec.error(str(exc))` |
| `_search_mode` entry | `rec.query(q, parsed, scenes)`; fallbacks → `rec.fallback(reason)` |
| variant loop | `rec.variant(query, fired, result_count)`; rate-defer / time-budget → `rec.note(...)` |
| after scoring loop | `rec.scored(best, threshold, scenes)` — hands over the existing `best` dict |
| `_passthrough` | `rec.passthrough_results(len(results))` |
| `_rss_mode` | `rec.rss_summary(items_total, rewritten, matched_pairs)` |

Supporting change: `_Scored` widens to carry the full `MatchScore` (today it keeps
only `confidence`) — no additional scoring work, the score is already computed.
Net effect on `_search_mode`: ~6 mechanical lines, no control-flow changes, no new
awaits.

## Grab & import wiring

1. **Decouple the webhook from the import-completer flag**: mount `import_router`
   whenever `import_completer.enabled` **or** `ui.enabled`. The route already
   no-ops when the completer is absent.
2. On `eventType == "Grab"` the handler extracts the release title and
   `downloadId` from the payload (exact field names verified against Whisparr's
   webhook payload during implementation) and calls `store.record_grab(...)` —
   in addition to the existing `completer.notify()`. Grab-recording failure must
   not block `notify()`.
3. `ImportCompleter.__init__` gains optional `store=None`; at its existing
   "import fired" point it calls `store.record_import(...)`; dry-run fires are
   recorded with `dry_run=True` ("would have imported"). The completer works
   unchanged when the store is `None`.
4. Docs: README + docker-compose example note to tick **On Grab** in the existing
   Whisparr Connect settings.

Non-goal: tracking Whisparr's own automated import pipeline. The import stage
appears only when Scenehound's import-completer fired the `ManualImport`.

## HTTP surface (`scenehound/ui_api.py`)

New isolated router, mounted in `app.py` when `config.ui.enabled`.

| Route | Auth | Returns |
|---|---|---|
| `GET /ui` | none (static shell, no data/secrets) | static HTML page (`HTMLResponse`; package resource `scenehound/static/ui.html`, read once at startup) |
| `GET /ui/api/sessions` | `apikey` query param vs `config.api_key`, 401 on mismatch (same mechanism as Torznab/webhook) | JSON `{sessions: [...], unmatched_grabs: [...], index: {size, age_seconds}}`, newest first |

Key handling in the page's JS: use `?apikey=` from `location.search` if present,
else localStorage; else render a one-time key input, verify with a probe fetch,
and persist to localStorage. A 401 on any poll (key rotated) clears the stored
key and re-prompts. The key is never embedded in page content served by the
server.

## Page (single static HTML, inline CSS + ~150 lines vanilla JS)

- **Header bar**: index size + age, session count, poll status dot.
- **Session list**, newest first; each session a collapsible card. Collapsed row:
  time, indexer, humanized ask ("LustyGrandmas 2023-07-04"), outcome badge
  (**Imported** / **Grabbed** / **Matched (3)** / **No matches** / **Passthrough**
  / **Error** / **RSS: 2 rewritten of 87**).
- **Expanded card**, four blocks:
  1. **Query chain** — what Whisparr asked → what Scenehound understood (site +
     dates, or why it couldn't) → each variant with fired/deferred + result count.
  2. **Candidates** — table by confidence: title, size, seeders, confidence bar,
     matched ✓/✗; "+N more dropped" footer when capped.
  3. **Why** — per-candidate plain-language translation (below).
  4. **Outcome** — the ladder with timestamps, or the failure reason.

### "Why" translation (pure presentation, in JS)

Rendered from `strong_signals` / `veto` / `detail` / the session's captured
`threshold`:

| Internal | Rendered |
|---|---|
| `veto: date-mismatch` | ✗ "Rejected: the release has a date, and it isn't the scene's date (±1 day)." |
| `veto: site-mismatch` | ✗ "Rejected: the title names a *different* studio from your wanted list." |
| `strong: date` | ✓ "Release date matches the scene's date" (+40) |
| `strong: site` | ✓ "Studio name appears in the title" (+35) |
| `strong: performer` | ✓ "Performer name appears in the title" (+35; "+15 for a second performer" when present) |
| `strong: title` | ✓ "The scene's full title appears in the release" (+40) |
| partial title points | "Title is similar (~N%)" (+ fractional points) |
| one strong signal | ⚠ "Needs two strong signals to match — only one found, so confidence is capped at 65." |
| verdict | "Confidence **82** ≥ threshold **75** → **matched**" (or the < form) |

Strings live in one JS lookup table; an unknown future signal degrades to its raw
name, never a broken render.

## Config (`scenehound/config.py`)

New frozen `UiConfig`, same YAML + env pattern as existing sections:

```yaml
ui:
  enabled: true          # SCENEHOUND_UI_ENABLED
  max_sessions: 50       # SCENEHOUND_UI_MAX_SESSIONS
  max_candidates: 200    # SCENEHOUND_UI_MAX_CANDIDATES
```

## Deployment artifacts (port & template updates)

The UI is served by the same uvicorn process on the **existing port 9797** — no
new internal port, no second server. Explicit changes:

- **Dockerfile**: no functional change (`EXPOSE 9797` already covers the UI; the
  `/healthz` HEALTHCHECK stays). A comment notes the port now also serves `/ui`.
- **docker-compose.example.yml**: comment on the existing `9797:9797` mapping that
  the web UI lives at `http://<host>:9797/ui`; add commented-out
  `SCENEHOUND_UI_ENABLED` / `SCENEHOUND_UI_MAX_SESSIONS` env lines alongside the
  import-completer block.
- **unraid/scenehound.xml**: `<WebUI>` changes from `/healthz` to
  `http://[IP]:[PORT:9797]/ui` (works unauthenticated as a shell; the page prompts
  for the API key once). The port `<Config>` description updates from "Torznab
  endpoint port" to mention the web UI; add an advanced `SCENEHOUND_UI_ENABLED`
  variable.
- **README**: new "Web UI" section — URL, key prompt behavior, On-Grab checkbox
  for grab tracking, env vars.

## Redaction

1. `CandidateTrace` stores no URLs: `link` / `enclosure` (which embed the Prowlarr
   API key) never enter the store.
2. GUIDs (occasionally URLs) get `apikey`-style query params stripped at capture;
   GUIDs are identity-only, never displayed.
3. `snapshot()` serializes only session dataclasses — config API keys cannot
   appear; covered by a poisoned-input test.

## Error handling

- Recorder/store methods cannot raise (internal try/except + `log.exception`).
- Webhook grab-recording failure cannot block `completer.notify()`.
- A UI-route exception is contained by FastAPI and cannot affect the Torznab router.
- UI disabled → `NullRecorder`, no store allocated, search behavior byte-identical.

## Testing (strict TDD)

- `test_observe.py` — ring eviction at `max_sessions`; candidate cap +
  `dropped_candidates`; matched-always-kept under cap pressure; grab correlation
  picks newest matching session; unmatched grabs bounded at 20; `record_import`
  stamps via `download_id`; dry-run flagged; internal exception swallowed + logged;
  snapshot contains no `apikey` substring given poisoned inputs.
- `test_api.py` additions — drive fake-Prowlarr fixtures through search mode and
  assert the committed session (variants fired/deferred, candidate scores, outcome);
  each passthrough `fallback_reason`; RSS summary shape; `ui.enabled=false` →
  store stays empty and search results byte-identical.
- `test_ui_api.py` — `/ui` serves HTML with no key; `/ui/api/sessions` 401s
  without/with wrong apikey and serves JSON with the right one; JSON shape
  (newest-first, index block); routes absent when disabled.
- `test_import_api.py` / wiring additions — `Grab` records a grab AND still rings
  the completer; `Test` ignored; webhook mounted with UI on + completer off;
  completer fire reaches `record_import`.
- `test_config.py` additions — `UiConfig` defaults + env overrides.
- The HTML/JS page is deliberately logic-light and not unit-tested (no JS
  toolchain, preserving no-build-step); the JSON endpoint is the tested contract;
  manual smoke pass for the page.

## Out of scope (v0.2.0)

- Persistence of sessions across restarts.
- Tracking Whisparr's automated (non-Scenehound) imports.
- SSE/websockets, multi-page navigation, historical analytics.
- Any write operations from the UI.
