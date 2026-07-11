#!/bin/sh
# Start as root, take ownership of the config volume as the requested PUID/PGID,
# then drop privileges and exec the server. Lets the container write its config
# and generated API key regardless of how the host owns the bind-mounted /config
# (Unraid appdata defaults to 99:100; plain docker hosts vary).
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"

mkdir -p /config
chown -R "${PUID}:${PGID}" /config 2>/dev/null || \
  echo "scenehound: warning: could not chown /config to ${PUID}:${PGID}; check volume permissions" >&2

exec gosu "${PUID}:${PGID}" "$@"
