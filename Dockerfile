ARG PYTHON_VERSION=3.12-slim-bookworm

FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# /data/media (public images) and /data/private (staff-only documents) are the
# local-storage roots; create them so a fresh local-backend deploy works even
# before the volumes are mounted over them (the GCS backend ignores them).
# Collected static lands in STATIC_ROOT (default <project>/staticfiles), written
# by collectstatic below.
RUN mkdir -p /djangoapp /data/media /data/private

WORKDIR /djangoapp

# install psycopg2 dependencies and curl for healthcheck
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN set -ex && \
    pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt && \
    rm -rf /root/.cache/

COPY . /djangoapp/

# collectstatic only writes static assets (WhiteNoise); it never touches the
# media backend. Run it with the no-config 'local' backend so the build doesn't
# depend on the runtime GCS env (STORAGE_BACKEND/GS_BUCKET_NAME live in fly.toml
# [env] / secrets, which aren't present at image-build time — and gcs now fails
# closed without a bucket). The real backend is set at runtime, not here.
RUN STORAGE_BACKEND=local python /djangoapp/manage.py collectstatic --no-input

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz/ || exit 1

# Worker sizing / recycling / bind / access-log are centralised in
# gunicorn.conf.py (env-driven). Fly runs this CMD; it migrates via its own
# release_command.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "artsite.wsgi"]

