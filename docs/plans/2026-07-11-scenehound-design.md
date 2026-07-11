# Scenehound — Design Document

**Date:** 2026-07-11
**Status:** Approved design, pre-implementation

## Problem

Whisparr v3 monitors adult scenes (metadata sourced from StashDB) and searches for
them through Prowlarr against private trackers (Empornium, HappyFappy). Whisparr's
search is rigid: it queries `<site name> <dd.mm.yyyy>` as a plain keyword term, e.g.

```
[Info] ReleaseSearchService: Searching indexer(s): [Empornium] for Term: [thatfetishgirl 07.07.2026], Offset: 0, Limit: 100, Categories: [6000]
```

Tracker release naming is inconsistent — releases are routinely missing the site
name, the date, or the title, and dates appear in many formats. The result is that
Whisparr frequently fails to find releases that exist, producing unreliable
automated downloads.

Whisparr itself holds rich metadata for every monitored scene (title, site, site
aliases, release date, performers). Scenehound uses that data to bridge the gap.

## Solution Overview

Scenehound is a **Torznab proxy** ("shim") that sits between Whisparr and Prowlarr.
It presents one fake Torznab indexer per real tracker. Whisparr queries Scenehound;
Scenehound resolves the query to a full scene fingerprint using Whisparr's own API,
fires smarter query variants at the real tracker via Prowlarr, scores every
candidate release against the fingerprint, and returns matches with canonically
rewritten titles that Whisparr can parse. Grabs, downloads, and imports then flow
through the normal Whisparr pipeline unchanged.

```
Whisparr ──torznab──▶ Scenehound ──torznab──▶ Prowlarr ──▶ Empornium / HappyFappy
                          │
                          └──REST──▶ Whisparr API (scene metadata, wanted list)
```

- **Search mode**: Whisparr sends a `site + date` term → Scenehound matches and returns rewritten results.
- **RSS mode**: Whisparr's periodic RSS sync arrives as an empty-query search → Scenehound fetches recent tracker uploads via Prowlarr, matches each against the monitored/wanted list, and rewrites titles of recognised releases. Same endpoint, same code path.

### Why this architecture (decision record)

Three approaches were considered:

- **A. Shim added directly to Whisparr as a Torznab indexer** ← chosen
- B. Shim registered inside Prowlarr (double-hop through Prowlarr; Prowlarr re-parses rewritten results, syncs the shim to all apps, muddies stats)
- C. Standalone RSS watcher pushing grabs via Whisparr's API (never helps searches; must independently authenticate to private trackers Prowlarr already handles)

A reuses Prowlarr's tracker auth (Empornium has no API — Prowlarr scrapes HTML with
session cookies), covers both search and RSS with one code path, and changes nothing
downstream of the grab. The real Empornium/HappyFappy indexers are unassigned from
Whisparr (they remain in Prowlarr for other apps); Whisparr talks only to Scenehound's
shimmed twins.

## Goals

1. Dramatically improve match/grab rates for monitored scenes on Empornium and HappyFappy.
2. Never make results worse than the status quo — every failure degrades to passthrough.
3. Never endanger the user's private tracker accounts — conservative, configurable rate limiting.
4. Zero manual tuning required — verbose structured logs instead of a UI.
5. Ship as a Docker container with a first-class Unraid template.

## Non-Goals & Accepted v1 Limitations

- **No UI.** Structured logs are the observability surface. A status page may come later.
- **No external metadata providers in v1.** Matching uses Whisparr's data (from StashDB) only. The metadata lookup is built behind a provider interface so e.g. ThePornDB can be added later without restructuring.
- **Retrievability limit (accepted):** tracker search engines match against release *title text only* (confirmed from the Empornium Cardigann definition: keyword search against the title field, no tag/description search). A release whose title contains none of our queryable strings (no site, no date, generic title) cannot be *retrieved* by search-mode queries no matter how good the matcher is. **RSS mode does not have this limit** — it sees every new upload and matches against the wanted list directly. Expectation: RSS mode is the primary catch mechanism going forward; search mode is best-effort for backlog.
- **Backlog drain rate (accepted):** with ~10k wanted items and deliberately conservative rate limits, a full backlog sweep takes days, processed opportunistically across Whisparr's search cycles. This is the correct trade for account safety.
- **Wrong-grab risk (accepted, mitigated):** a false-positive match above threshold grabs the wrong release. Mitigations: two-strong-signal rule, contradiction vetoes, conservative threshold, full audit logging. Blast radius is one wrong download — Whisparr's import-time parse usually flags mismatched files for manual import, though the queue mapping means this is not guaranteed.

## Components

One Python 3.12 service. FastAPI + httpx. No database — all state is fetched from
Whisparr/Prowlarr and cached in memory; restarts are always safe.

| Component | Responsibility |
|---|---|
| **Torznab endpoint** | Serves `/indexer/{slug}/api` (`t=caps`, `t=search`). Parses inbound queries, returns rewritten Torznab XML. Only inbound surface. Guarded by a Scenehound API key. |
| **Whisparr client** | Pages the monitored/wanted scene list from Whisparr's API into the wanted-index; background refresh (~15 min TTL). |
| **Prowlarr client** | Sends query variants to the real indexer's Torznab endpoint in Prowlarr; returns raw candidate releases. |
| **Wanted-index** | In-memory index of monitored scenes (see Scaling). Serves both search-mode scene resolution and RSS-mode candidate lookup. |
| **Query planner** | Generates search variants from a scene fingerprint, adaptively (see Rate Limiting). |
| **Matcher** | Scores candidate release titles against scene fingerprints → confidence 0–100. Pure functions, zero I/O, heavily unit-tested. |
| **Title rewriter** | Emits canonical titles from the fingerprint + preserved quality tokens. Pure functions. |
| **Rate limiter** | Per-indexer token bucket gating all Prowlarr-bound queries. |
| **Config** | YAML file + env-var overrides. |

Design rule: matcher and rewriter are pure logic with no I/O, so matching accuracy
can be tested against a corpus of real tracker titles.

## Matching Pipeline (search mode)

1. **Parse the query.** `thatfetishgirl 07.07.2026` → `{site_token, date}`. The date
   format is `dd.mm.yyyy` (confirmed from live logs). For ambiguous dates (day ≤ 12),
   both interpretations are checked against the wanted-index; if only one matches a
   monitored scene the ambiguity is resolved conclusively, if both match a second
   signal is required downstream. Unparseable queries → passthrough.
2. **Resolve the scene** against the wanted-index (local lookup, no API round-trip).
   Site tokens are squash-normalized (`scottstarkstudios` ≡ "Scott Stark Studios";
   case/punctuation/spacing stripped) and checked against site names and aliases.
   - Exactly one scene for site+date → proceed with its fingerprint
     `{site, site_aliases, date, title, performers[]}`.
   - Multiple scenes for site+date → proceed, but title/performer evidence must
     disambiguate before any result is returned for one of them.
   - No scene resolved → **passthrough**: forward the query to Prowlarr verbatim,
     return results unrewritten.
3. **Plan queries** (adaptive — see Rate Limiting). Candidate variants, deduplicated,
   all title-shaped (tracker search only matches titles): site+date in multiple
   formats (`2026-07-07`, `07.07.26`, `26.07.07`, …), site alone, performer+date,
   distinctive title words. Hard cap per search: `max_queries_per_search` (default 5).
4. **Score candidates.** Every release returned by any variant is tokenized and
   scored against the fingerprint:

   | Signal | Contribution |
   |---|---|
   | Date in title — ~10 formats, 2-digit years, dd/mm and mm/dd both tried | strong |
   | Site name/alias fuzzy match (rapidfuzz, squash-normalized) | strong |
   | Performer name(s) present | strong; multiple performers ≈ conclusive |
   | Title similarity (token-set ratio, quality/codec junk tokens excluded) | medium |
   | **Contradiction**: a clearly-parsed *different* date, or a *different* site | heavy penalty — effective veto |

   **Two-strong-signal rule:** no release clears the threshold (default 75) on one
   strong signal alone. Two independent strong signals (date+performer, site+date,
   site+performer) are required.
5. **Emit.** Everything ≥ threshold is returned, best score first, titles rewritten.
   Sub-threshold candidates are logged with scores and the specific signal that
   fell short.

## RSS Mode

Torznab RSS sync is an empty-query search. Scenehound fetches recent uploads from
the real indexer via Prowlarr (one request — no fan-out) and matches each release
against the wanted-index:

- Release title parses a date → score only against scenes within ±1 day (date index).
- No date → score only against scenes sharing ≥1 distinctive token (token index).
- No date and no token overlap → provably cannot reach threshold (two-signal rule) → skipped without scoring.

Matched releases get rewritten titles; unmatched releases pass through unmodified
(Whisparr may still recognise well-named ones natively). Result: each RSS pass
scores each release against ~5–50 candidates instead of 10k.

## Scaling: the Wanted-Index

The wanted list is ~10k scenes and growing. Naive all-pairs scoring per RSS pass
(~100 releases × 10k scenes, multi-signal) is off the table. The index:

- **Fetch**: paged from Whisparr's API in a background task, ~15 min TTL; a few MB in memory.
- **Date index**: scenes bucketed by release date.
- **Token index**: inverted index over squash-normalized site names/aliases, performer names, and distinctive title tokens.
- **Lossless pre-filter**: because matching requires two strong signals, any (release, scene) pair sharing zero strong signals can never match — pre-filtering by shared date bucket or shared token drops no true positives by construction.

The same index resolves search-mode queries (site token + date → scene), replacing
per-search Whisparr API calls.

## Rate Limiting & Tracker Safety

Ground truth (from the Prowlarr Empornium definition and Prowlarr source):

- Empornium/HappyFappy access is **HTML scraping of the search page** with session cookies — identical to human browsing; the risk is looking like a bot, and the failure mode is account warnings/loss, not a 429.
- Prowlarr enforces a hard floor of **2 s between requests per indexer** and queues internally. Scenehound's limits sit **on top of** this floor.

Defenses, both on by default:

1. **Adaptive query planning**: fire the single best variant first; escalate to
   further variants only if nothing scores above ~50. Typical well-named searches
   cost 1 tracker query; hard ones cost up to the cap. Expected amplification
   ~1.3×, not 5×.
2. **Per-indexer token bucket**: default burst 4, refill 1 token / 15 s
   (≈4 queries/min sustained). Interactive searches feel instant; bulk operations
   (Whisparr "search all missing") throttle to slower-than-human browsing pace.
   When the bucket is empty the search returns an empty result immediately, logged
   as `rate-deferred` — Whisparr retries the item on its next natural cycle.
   Deliberately no queueing into Whisparr's 60 s indexer timeout.

Both knobs are configurable; defaults are set for a very risk-averse posture.

## Title Rewriting & Result Passthrough

Rewritten title format (what Whisparr's parser expects):

```
Site.Name.YYYY-MM-DD.Scene.Title.XXX.<preserved-tokens>
e.g. ThatFetishGirl.2026-07-07.Some.Scene.Title.XXX.1080p.MP4-GroupName
```

- Site, date, title come from the **fingerprint** (authoritative), never the messy source title.
- Quality/codec/source/group tokens are **extracted from the original title and preserved**, including sloppy forms (`4k`, `UHD`, `[1080]`, `h265`, …).
- **No quality tokens found → none emitted.** Scenehound never fabricates quality
  (no size-based estimation — explicitly rejected). Whisparr parses these as
  Unknown quality; see Prerequisites.
- Everything else in the Torznab item passes through verbatim: download `link`
  (already points at Prowlarr's `/download` proxy, so grabs use Prowlarr's tracker
  auth exactly as today), GUID (so Whisparr dedupe/history works), size, seeders,
  leechers, categories. The original title is preserved in a custom XML attribute
  and in the logs for auditability.
- `t=caps` is served statically: `q` search only, categories 6000/6010.

## Error Handling

Guiding rule: **degrade to passthrough, never block.**

| Failure | Behaviour |
|---|---|
| Whisparr API unreachable / index stale and scene unresolvable | Passthrough: forward query verbatim to Prowlarr, return results unrewritten (= status quo behaviour) |
| Prowlarr unreachable / errors | Proper Torznab error response; Whisparr's native indexer-failure handling (backoff, health warning) takes over |
| Unparseable / unexpected query shape | Passthrough, logged at `warning` to surface new query shapes |
| Slow tracker | Per-request time budget ~45 s (under Whisparr's 60 s indexer timeout); return whatever matched within budget |
| Rate limit bucket empty | Immediate empty result, `rate-deferred` log line |
| Process crash | Stateless; Docker restart policy + healthcheck make restart always safe |

## Configuration

One YAML file, every value overridable by env var (Unraid-friendly):

```yaml
whisparr:
  url: http://192.168.1.x:6969
  api_key: !env WHISPARR_API_KEY
prowlarr:
  url: http://192.168.1.x:9696
  api_key: !env PROWLARR_API_KEY

indexers:
  - slug: empornium          # Scenehound endpoint: /indexer/empornium/api
    prowlarr_id: 12          # the real indexer's ID in Prowlarr
  - slug: happyfappy
    prowlarr_id: 15

matching:
  threshold: 75              # min confidence to return a result
  max_queries_per_search: 5

rate_limit:
  burst: 4
  refill_seconds: 15         # 1 token per N seconds, per indexer

log_level: info              # debug = full per-candidate scoring detail
```

Scenehound has its own API key (generated on first run if unset) which is pasted
into Whisparr's indexer settings, so it is not an open unauthenticated proxy.

## Deployment

- **Image**: `python:3.12-slim` base, non-root user, port `9797`, single `/config`
  volume (config + generated key), `/healthz` endpoint wired to Docker `HEALTHCHECK`.
- **CI**: GitHub-Actions-style workflow (Forgejo Actions compatible) building and
  publishing the image on tag.
- **Unraid template** (`unraid/scenehound.xml`, in-repo from day one):
  - `<Config>` entries for Whisparr/Prowlarr URLs + API keys, threshold, log level
    (everything essential settable from the Unraid UI; the indexer list stays in
    YAML since templates cannot express lists)
  - `/config` path mapping, WebUI port mapping, icon, overview, support link
  - `<Network>` default `bridge`, category `Downloaders:`
  - Installable via "Add Container → Template" pointing at the repo raw URL;
    same XML is CA-submission-ready if ever wanted.

## Logging

Stdout, Docker-native (visible in Unraid's log viewer).

- `info`: one line per search — query → resolved scene → variants fired → N candidates → M returned, with scores.
- `debug`: full per-candidate signal breakdown.
- Rejected candidates always log *which* signal failed — this is the tuning feedback loop.
- Wrong grabs are traceable: original tracker title is logged alongside every rewrite.

## Testing

1. **Matcher corpus tests** (bulk of the suite): fixtures of real messy titles from
   Empornium/HappyFappy paired with expected verdicts. Pure-function tests, no
   mocks. Production mismatches get added to the corpus as regression tests — the
   accuracy ratchet. (Seed corpus: an RSS/search dump from both trackers, gathered
   during implementation.)
2. **Endpoint tests**: FastAPI test client with mocked Whisparr/Prowlarr responses;
   verifies Torznab XML shape, caps, passthrough fallbacks, rate-limit behaviour.
3. **Live smoke test** (documented manual procedure): add the indexer to Whisparr,
   hit Test, run one interactive search, confirm a grab flows end-to-end.

## Setup Prerequisites (user-facing, documented in README)

1. The Whisparr quality profile used for monitored scenes **must allow "Unknown"**
   quality — quality-profile filtering happens at grab time, and honest rewrites of
   token-less releases parse as Unknown. (Post-download, import-time analysis takes
   over with the actual file.)
2. The real Empornium/HappyFappy indexers must be unassigned from Whisparr (left
   in Prowlarr for other apps); Scenehound's shimmed indexers are added to Whisparr
   in their place.

## Future Extensions (explicitly out of v1)

- External metadata providers (ThePornDB, …) behind the existing provider interface.
- Additional indexers (config is already a list).
- Status page / web UI, if logs ever prove insufficient.
- Local full-catalogue index of tracker listings to defeat the search-retrievability
  limit for backlog items.
