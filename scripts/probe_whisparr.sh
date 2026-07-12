#!/usr/bin/env bash
# Probe Whisparr v3 (eros) API shapes for the import-completer. Read-only (GETs only).
# Usage: WHISPARR_URL=http://whisparr-host:6979 WHISPARR_API_KEY=xxx ./scripts/probe_whisparr.sh
# Run while at least one download is held with "matched to movie by ID / Manual Import required".
# Sanitize output (strip infohashes/paths you don't want committed) before saving as fixtures.
set -euo pipefail
: "${WHISPARR_URL:?set WHISPARR_URL}"; : "${WHISPARR_API_KEY:?set WHISPARR_API_KEY}"
H=(-H "X-Api-Key: ${WHISPARR_API_KEY}")
base="${WHISPARR_URL%/}"

echo "== /api/v3/queue (find held items: trackedDownloadState, statusMessages, movieId, downloadId) =="
curl -fsS "${H[@]}" "${base}/api/v3/queue?page=1&pageSize=50&includeMovie=true" | tee /tmp/wh_queue.json | python3 -m json.tool | head -120

dl="$(python3 -c 'import json,sys; d=json.load(open("/tmp/wh_queue.json")); \
print(next((r.get("downloadId","") for r in d.get("records",[]) \
if "id" in str(r.get("statusMessages","")).lower() or "manual" in str(r.get("statusMessages","")).lower()), ""))')"
echo "== held downloadId detected: ${dl:-<none>} =="

if [ -n "${dl}" ]; then
  echo "== /api/v3/manualimport?downloadId=… (candidate shape: movie, rejections, quality, languages, sample flags) =="
  curl -fsS "${H[@]}" "${base}/api/v3/manualimport?downloadId=${dl}&filterExistingFiles=true" \
    | tee /tmp/wh_manualimport.json | python3 -m json.tool | head -160
fi

echo
echo "NEXT: verify a wanted-record 'id' equals a queue record 'movieId' (scene_id == movieId):"
echo "  curl -s ${H[*]} '${base}/api/v3/wanted/missing?pageSize=5' | python3 -m json.tool | grep -E '\"id\"|\"title\"'"
echo "SOURCE-READ (no safe write probe): confirm the ManualImport command body + exact webhook eventType"
echo "  in the Whisparr eros branch (Radarr lineage: POST /api/v3/command {name:'ManualImport', importMode, files:[…]})."
echo "SAVE sanitized: /tmp/wh_queue.json -> tests/fixtures/whisparr_queue_sample.json"
echo "                /tmp/wh_manualimport.json -> tests/fixtures/whisparr_manualimport_sample.json"
