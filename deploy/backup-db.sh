#!/bin/bash
# Back up the artsite Postgres database to a dated, pg_restore-compatible dump,
# pruning ones older than the retention window.
#
# Built for the rootless Podman Quadlet deployment: it runs pg_dump *inside* the
# artsite-db container (so the host needs no psql client) and writes the dump to
# BACKUP_DIR — point that at your NAS. A logical dump like this is the SAFE way
# to land Postgres data on NFS/NAS; never put the live data directory there.
#
# Schedule it with the units in deploy/systemd/:
#   cp deploy/systemd/artsite-backup.* ~/.config/systemd/user/
#   # edit artsite-backup.service so ExecStart points at this script
#   systemctl --user daemon-reload
#   systemctl --user enable --now artsite-backup.timer
#
# Config (env, with defaults): ARTSITE_DB_CONTAINER, BACKUP_DIR, RETENTION_DAYS,
# POSTGRES_DB, POSTGRES_USER. The systemd unit reads POSTGRES_* from artsite.env.
set -euo pipefail

CONTAINER="${ARTSITE_DB_CONTAINER:-artsite-db}"
BACKUP_DIR="${BACKUP_DIR:-/srv/artsite/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DB="${POSTGRES_DB:-artsite}"
DB_USER="${POSTGRES_USER:-artsite}"

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y-%m-%d_%H%M%S)"
out="$BACKUP_DIR/artsite-$stamp.dump"

# Write to a temp file first and rename on success, so a failed/partial dump
# never lands as a "good" backup (the trap cleans the temp on any early exit).
tmp="$(mktemp "$BACKUP_DIR/.artsite-$stamp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

podman exec "$CONTAINER" pg_dump -Fc -U "$DB_USER" "$DB" > "$tmp"
if [ ! -s "$tmp" ]; then
    echo "ERROR: dump is empty — leaving previous backups untouched." >&2
    exit 1
fi
mv "$tmp" "$out"
trap - EXIT
echo "Wrote $out ($(du -h "$out" | cut -f1))"

# Prune dumps older than the retention window.
find "$BACKUP_DIR" -name 'artsite-*.dump' -type f -mtime "+$RETENTION_DAYS" -delete
