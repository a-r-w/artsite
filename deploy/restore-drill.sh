#!/bin/bash
# Periodic restore drill — proves the backups are USABLE, not merely present.
#
# It restores the newest DB dump (from backup-db.sh) into a THROWAWAY Postgres
# container, then runs `manage.py verify_media` (in the artsite image) against the
# media backup (from backup-media.sh). That confirms two things together: the dump
# actually restores, and every file the restored DB references is present in the
# media backup. Exits non-zero if either fails, so a scheduled run flags a broken
# backup before you need it.
#
# Touches nothing live: a throwaway container on a throwaway network, read-only
# mounts of the backup. Requires the media backup to be on a LOCAL path (so it can
# be mounted). verify_media is scoped to the public store, so it validates the
# media backup; the private-documents backup is replicated by backup-media.sh but
# not cross-checked here.
#
# Config (env, defaults): BACKUP_DIR, MEDIA_BACKUP_DEST (local), ARTSITE_IMAGE,
# POSTGRES_DB, POSTGRES_USER.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/srv/artsite/backups}"
DEST="${MEDIA_BACKUP_DEST:?set MEDIA_BACKUP_DEST to the LOCAL media-backup path}"
IMAGE="${ARTSITE_IMAGE:-localhost/artsite:latest}"
DB="${POSTGRES_DB:-artsite}"
DB_USER="${POSTGRES_USER:-artsite}"
PG_IMAGE="docker.io/library/postgres:16-alpine"

dump="$(ls -1t "$BACKUP_DIR"/artsite-*.dump 2>/dev/null | head -1 || true)"
[ -n "$dump" ] || { echo "ERROR: no artsite-*.dump in $BACKUP_DIR" >&2; exit 1; }
echo "Restore drill using: $dump"

net="artsite-drill-$$"
dbc="artsite-drill-db-$$"
pw="drill-$$"
cleanup() { podman rm -f "$dbc" >/dev/null 2>&1 || true; podman network rm "$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT

podman network create "$net" >/dev/null
podman run -d --name "$dbc" --network "$net" \
    -e POSTGRES_DB="$DB" -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$pw" \
    "$PG_IMAGE" >/dev/null

echo -n "Waiting for throwaway Postgres"
ready=0
for _ in $(seq 1 30); do
    if podman exec "$dbc" pg_isready -U "$DB_USER" -d "$DB" >/dev/null 2>&1; then ready=1; break; fi
    echo -n .; sleep 1
done
echo
[ "$ready" = 1 ] || { echo "ERROR: throwaway Postgres never became ready" >&2; exit 1; }

podman cp "$dump" "$dbc:/tmp/drill.dump"
podman exec "$dbc" pg_restore --no-owner --no-acl --clean --if-exists \
    -U "$DB_USER" -d "$DB" /tmp/drill.dump

# verify_media exits non-zero if any DB-referenced public file is missing from the
# mounted backup. ENVIRONMENT=development so it needs no SECRET_KEY/ALLOWED_HOSTS.
podman run --rm --network "$net" \
    -e ENVIRONMENT=development -e STORAGE_BACKEND=local \
    -e DATABASE_URL="postgres://$DB_USER:$pw@$dbc:5432/$DB" \
    -e MEDIA_ROOT=/media -e PRIVATE_MEDIA_ROOT=/private \
    -v "$DEST/media:/media:ro,Z" -v "$DEST/private:/private:ro,Z" \
    "$IMAGE" python manage.py verify_media

echo "Restore drill PASSED — dump restored and all referenced media present in the backup."
