#!/bin/sh
# Container entrypoint for the docker-compose deployment: bring the schema up to
# date, optionally bootstrap a first admin and sample data, then serve.
# (Fly uses its own release_command for migrate and the Dockerfile CMD for
# gunicorn, so it does not use this script.)
set -e

python manage.py migrate --noinput

# First admin, only if credentials are provided and not already created.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null \
        && echo "Created superuser '$DJANGO_SUPERUSER_USERNAME'." \
        || echo "Superuser '$DJANGO_SUPERUSER_USERNAME' already exists; skipping."
fi

# Optional sample data on first boot (SEED_DEMO=1); idempotent.
if [ "$SEED_DEMO" = "1" ]; then
    python manage.py seed_demo
fi

# Sizing/recycling/bind come from gunicorn.conf.py (compose sets
# GUNICORN_BIND=0.0.0.0:8000 since its bridge network is IPv4, unlike Fly's IPv6).
exec gunicorn -c gunicorn.conf.py artsite.wsgi
