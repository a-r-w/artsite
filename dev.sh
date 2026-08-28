#!/bin/bash
# Local development server. Zero setup: local filesystem media + a throwaway
# SQLite database, so a fresh clone just runs ./dev.sh.
#
# To develop against your own backend/DB (e.g. GCS + Postgres), copy
# dev.local.sh.example to dev.local.sh (gitignored) and set your vars there —
# it's sourced below and takes precedence over the defaults.
set -e
here="$(cd "$(dirname "$0")" && pwd)"

# Personal, machine-specific overrides (gitignored), if present.
if [ -f "$here/dev.local.sh" ]; then
    source "$here/dev.local.sh"
fi

# Friendly defaults for anything the override didn't set.
export STORAGE_BACKEND="${STORAGE_BACKEND:-local}"
export MEDIA_ROOT="${MEDIA_ROOT:-$here/media}"
export PRIVATE_MEDIA_ROOT="${PRIVATE_MEDIA_ROOT:-$here/private}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$here/db.sqlite3}"

python manage.py migrate
python manage.py runserver "$@"
