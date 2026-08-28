#!/bin/bash
# Replicate the artsite media + private documents to an OFF-SITE location.
#
# On the self-host (local storage) backend the images ARE the collection (the DB
# stores only filenames) and the staff documents (receipts, valuations) have no
# other copy — so the NAS is a single point of failure. This rsyncs both trees to
# MEDIA_BACKUP_DEST: point it at a second machine/disk, e.g. an rsync-over-ssh
# target (user@host:/srv/artsite-backup) or another mount.
#
# Additive by DEFAULT: a file deleted from the source is KEPT in the backup, so an
# accidental delete stays recoverable. Set MIRROR=1 for a true mirror (rsync
# --delete). Either way, give the destination its own snapshots if you can.
#
# Pairs with backup-db.sh; schedule with deploy/systemd/artsite-media-backup.{service,timer}.
#
# Config (env, with defaults): MEDIA_DIR, PRIVATE_DIR, MEDIA_BACKUP_DEST (required).
set -euo pipefail

MEDIA_DIR="${MEDIA_DIR:-/srv/artsite/media}"
PRIVATE_DIR="${PRIVATE_DIR:-/srv/artsite/private}"
DEST="${MEDIA_BACKUP_DEST:?set MEDIA_BACKUP_DEST to an off-site path or user@host:/path}"

opts=(--archive --human-readable --partial)
[ "${MIRROR:-0}" = "1" ] && opts+=(--delete)

# Local destination (no "host:" prefix): make sure the base exists. A remote
# target's base path must already exist.
case "$DEST" in
    *:*) ;;                       # remote (rsync over ssh) — leave to the remote
    *) mkdir -p "$DEST" ;;
esac

status=0
for src in "$MEDIA_DIR" "$PRIVATE_DIR"; do
    if [ ! -d "$src" ]; then
        echo "WARN: $src does not exist — skipping." >&2
        continue
    fi
    name="$(basename "$src")"
    echo "Backing up $src/ -> $DEST/$name/"
    # Trailing slash on src copies its CONTENTS into the same-named subdir.
    rsync "${opts[@]}" "$src/" "$DEST/$name/" || { echo "ERROR: rsync of $src failed" >&2; status=1; }
done

[ "$status" = 0 ] && echo "Media backup complete."
exit "$status"
