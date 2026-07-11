# Scenehound

Torznab matching proxy between [Whisparr v3] and [Prowlarr]. Whisparr searches
for scenes by exact `site + date`; private-tracker release naming is chaos.
Scenehound sits between them, resolves Whisparr's rigid queries against its own
scene metadata (title, performers, date, site), hunts the tracker via Prowlarr
with smarter query variants, scores every candidate, and returns matches with
canonical titles Whisparr can actually parse. Everything downstream — grabs,
downloads, imports — is stock Whisparr.

Design: `docs/plans/2026-07-11-scenehound-design.md`.

## How it works

    Whisparr ──torznab──▶ Scenehound ──torznab──▶ Prowlarr ──▶ trackers
                              └──REST──▶ Whisparr API (wanted list)

- **Search**: `thatfetishgirl 07.07.2026` → scene fingerprint → adaptive query
  variants → candidates scored (two independent strong signals required) →
  rewritten results returned.
- **RSS sync**: every new tracker upload is matched against your entire wanted
  list; recognised releases get canonical titles.
- **Any failure → passthrough**: results flow unmodified, never worse than stock.
- **Tracker-safe**: per-indexer token bucket (default: burst 4, one query per
  15 s sustained) on top of Prowlarr's own 2 s floor. Deliberately conservative.

## Setup prerequisites

1. **Your Whisparr quality profile must allow "Unknown"** (Settings → Profiles).
   Quality filtering happens at grab time; honestly-rewritten releases with no
   quality tokens parse as Unknown and would otherwise be rejected before
   download. Scenehound never invents quality it can't see.
2. **Unassign the real tracker indexers from Whisparr** (keep them in Prowlarr
   for other apps). Whisparr should reach those trackers only through Scenehound,
   or you'll get duplicate results.

## Install (Unraid)

Add Container → Template → point at `unraid/scenehound.xml` raw URL. Fill in
the Whisparr/Prowlarr URLs and API keys. Then create
`/mnt/user/appdata/scenehound/config.yaml`:

    indexers:
      - slug: empornium        # -> http://SERVER:9797/indexer/empornium/api
        prowlarr_id: 12        # Prowlarr indexer ID (visible in its URL when edited)
      - slug: happyfappy
        prowlarr_id: 15

**PUID / PGID**: default to `99` / `100` (Unraid's `nobody:users`). The
container runs as root only to `chown /config` to `PUID:PGID` on start, then
drops to that user — so no manual `chown` of the appdata path is ever needed.
Change PUID/PGID (both are advanced fields) only if your appdata directory is
owned by a different user.

Start the container. Read the Scenehound API key from
`/mnt/user/appdata/scenehound/apikey`.

## Add to Whisparr

For each indexer: Settings → Indexers → Add → Torznab:

- URL: `http://SERVER:9797/indexer/<slug>` (Whisparr appends `/api`)
- API Key: contents of the `apikey` file
- Categories: 6000

Press Test — a green check means the whole chain works. Run one interactive
search on a monitored scene and watch `docker logs scenehound`.

## Logs are the UI

    docker logs -f scenehound

`info` shows one line per search/RSS decision with scores. `debug` shows every
candidate's per-signal breakdown. A rejected match always says which signal
fell short. Wrong grab? The original tracker title is in the log line and in
the `scenehound_original_title` attribute of every rewritten result — add the
case to `tests/fixtures/corpus.yaml` and it becomes a regression test.

## Not in v1 (deliberate)

- External metadata providers (ThePornDB etc.) — the interface exists, nothing
  plugs in yet.
- Defeating tracker search's title-only retrieval for ancient backlog items:
  RSS catches things going forward; search mode is best-effort for the past.
- A web UI.
