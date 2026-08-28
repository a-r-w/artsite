"""Django settings for the artsite project."""

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Fail closed on an unrecognized ENVIRONMENT. Previously `PROD = ENVIRONMENT ==
# 'production'` meant any typo ('Production', 'prod') or an unset value in a new
# deploy silently fell back to DEBUG=True + the committed dev SECRET_KEY. Reject
# anything outside the known set so a misconfigured deploy refuses to start
# rather than booting insecure. (Unset defaults to development for local work.)
_ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development')
if _ENVIRONMENT not in ('production', 'development'):
    raise ImproperlyConfigured(f"Unrecognized ENVIRONMENT={_ENVIRONMENT!r}; expected 'production' or 'development'.")
PROD = _ENVIRONMENT == 'production'

if PROD:
    SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
else:
    SECRET_KEY = 'django-insecure-*$+44#2^c2p)(hby*rq&jukeb4$g8@sv5kea%b_km0@)^!qz4w'

DEBUG = not PROD


def _csv_env(name, default):
    """Parse a comma-separated env var into a list of trimmed, non-empty values.

    Returns `default` (copied) when the var is unset; an explicit empty/blank
    value yields []. Used for the host / CSRF allowlists below.
    """
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(',') if item.strip()]


# Host / CSRF allowlists are env-driven so a deployment isn't tied to domains
# baked into the code. localhost/127.0.0.1 are ALWAYS allowed — the in-container
# healthcheck hits http://localhost:8000/ and local dev needs them — so the env
# var only carries the public hostname(s). Production must name at least one, or
# it refuses to start: a healthy container quietly serving 400s to every real
# request (healthcheck passes on localhost) is a nasty silent failure.
_PUBLIC_HOSTS = _csv_env('ALLOWED_HOSTS', [])
if PROD and not _PUBLIC_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must list the public hostname(s) in production (comma-separated).')
ALLOWED_HOSTS = ['localhost', '127.0.0.1', *_PUBLIC_HOSTS]
# CSRF trusted origins need a scheme. Default to https:// for each public host
# (the prod domains are TLS-only); localhost/IPs are skipped (same-origin dev
# over http needs no entry). Override wholesale with CSRF_TRUSTED_ORIGINS.
CSRF_TRUSTED_ORIGINS = _csv_env(
    'CSRF_TRUSTED_ORIGINS',
    [f'https://{h}' for h in _PUBLIC_HOSTS if '.' in h and not h.replace('.', '').isdigit()],
)

INSTALLED_APPS = [
    'art.apps.ArtConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'easy_thumbnails',
    'django_htmx',
    'axes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # After WhiteNoise (static files don't need it) but before the views, so the
    # per-request nonce is set before templates render. Adds the CSP header.
    'art.middleware.ContentSecurityPolicyMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    # Must be LAST: wraps the response so a locked-out login attempt is turned
    # into the lockout response (django-axes requirement).
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'artsite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'art.context_processors.site_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'artsite.wsgi.application'

# DATABASE_URL drives the database. Production must set it (fail closed rather
# than silently falling back to SQLite); in development it defaults to a
# throwaway SQLite file so a fresh clone runs `migrate`/`runserver` with no setup.
_database_url = os.environ.get('DATABASE_URL')
if PROD and not _database_url:
    raise ImproperlyConfigured('DATABASE_URL must be set in production.')
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
        conn_health_checks=True,
    ),
}
# A bare manage.py command with DATABASE_URL unset hits the SQLite fallback, not
# your real database — so e.g. `dumpdata` would silently read an empty DB. Surface
# it. (Skipped under `manage.py test`, which supplies its own in-memory database.)
if not _database_url and 'test' not in sys.argv:
    logging.getLogger('artsite').warning(
        'DATABASE_URL not set — using the local SQLite database at %s. Set '
        'DATABASE_URL to read/write your real database (e.g. Postgres).',
        BASE_DIR / 'db.sqlite3',
    )
# Wrap each request in a DB transaction so a view that writes is all-or-nothing:
# a failure mid-write rolls back instead of leaving a partial/inconsistent row.
# Storage isn't transactional, so an uploaded file can still outlive a
# rolled-back save — cleanup_orphan_images reconciles any such orphan.
DATABASES['default']['ATOMIC_REQUESTS'] = True

CONN_HEALTH_CHECKS = True

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- Login rate-limiting (django-axes) ---
# The stock LoginView (/curate/login/) and the Django admin (/admin/login/)
# accept unlimited password attempts. Axes throttles brute force at the app
# layer, so ONE config protects both deployments (Fly + self-host) — no per-edge
# Caddy/Fly throttle to maintain. AxesStandaloneBackend must come BEFORE the
# default ModelBackend; it only enforces the lockout (it doesn't authenticate),
# deferring the credential check to ModelBackend.
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]
AXES_FAILURE_LIMIT = 5  # failed attempts before lockout
AXES_COOLOFF_TIME = timedelta(minutes=30)  # lockout window, then it auto-clears
AXES_RESET_ON_SUCCESS = True  # a good login clears that source's failure tally
# Don't let attempts made *during* a lockout roll the cooloff forward (axes
# defaults this True): keeps the 30-minute window fixed — so the lockout-page
# copy stays honest and a panic-retrying curator can't extend their own lockout.
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
# Lock the specific (username, IP) pair, not the bare IP or bare username: it
# stops a brute-forcer hammering an account from one source, yet never locks the
# curator out from a different network and never escalates to a site-wide login
# outage. The real client IP is resolved per deployment edge in production
# (see the proxy block below).
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_LOCKOUT_TEMPLATE = 'curate/lockout.html'

# Locale is env-driven so a non-US deployment needn't edit source. TIME_ZONE
# affects how stored (UTC) datetimes display; dates use international formats.
# Default currency / measurement units / site name are editable in /curate/.
LANGUAGE_CODE = os.environ.get('LANGUAGE_CODE', 'en-us')
TIME_ZONE = os.environ.get('TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

# Media/image storage backend — two first-class, supported deployments:
#   'gcs'   — Google Cloud Storage via django-storages (the Fly.io deployment).
#   'local' — Django's FileSystemStorage under MEDIA_ROOT (the self-hosted
#             podman + NAS deployment), served at MEDIA_URL by a reverse proxy.
# Default 'local' so a fresh clone runs with zero cloud setup (and a forgotten
# STORAGE_BACKEND can't silently fail on a missing GCS bucket). The cloud
# deployments set STORAGE_BACKEND=gcs explicitly (fly.toml), so they're
# unaffected. The same dotted path drives BOTH STORAGES['default'] and
# easy-thumbnails' THUMBNAIL_DEFAULT_STORAGE from one source (_MEDIA_BACKEND), so
# source images and their thumbnails can never end up split across backends.
STORAGE_BACKEND = os.environ.get('STORAGE_BACKEND', 'local')


def storage_backend_path(name):
    """Dotted path of the media-storage backend class for a STORAGE_BACKEND
    value. Fail closed (ImproperlyConfigured) on an unknown value, like
    ENVIRONMENT above, so a misconfigured deploy refuses to start rather than
    silently using the wrong backend."""
    backends = {
        'gcs': 'storages.backends.gcloud.GoogleCloudStorage',
        'local': 'django.core.files.storage.FileSystemStorage',
    }
    try:
        return backends[name]
    except KeyError:
        raise ImproperlyConfigured(
            f'Unrecognized STORAGE_BACKEND={name!r}; expected one of {sorted(backends)}.'
        ) from None


_MEDIA_BACKEND = storage_backend_path(STORAGE_BACKEND)

# In-bucket path prefix for the GCS backend (also the base for the private
# prefix). Defaults to art / art-dev by environment; override with GS_LOCATION
# (e.g. a self-hoster sharing one bucket across projects).
_GS_LOCATION = os.environ.get('GS_LOCATION') or ('art' if PROD else 'art-dev')

# Staff-only documents (receipts, valuations, …) live in a SEPARATE private store
# the public never reaches: no reverse-proxy route and no signed URLs are ever
# handed out — the staff-gated download view streams the bytes. The backends
# (art.storage) refuse to emit a public URL at all. Local stores OUTSIDE the
# media tree (PRIVATE_MEDIA_ROOT); GCS uses a private in-bucket prefix.
PRIVATE_MEDIA_ROOT = os.environ.get('PRIVATE_MEDIA_ROOT', BASE_DIR / 'private')


def _private_storage_config(backend):
    if backend == 'local':
        return {
            'BACKEND': 'art.storage.PrivateFileSystemStorage',
            'OPTIONS': {'location': PRIVATE_MEDIA_ROOT},
        }
    return {
        'BACKEND': 'art.storage.PrivateGoogleCloudStorage',
        # location: a SIBLING prefix, NOT a child of _GS_LOCATION. If it lived
        # under the public location, cleanup_orphan_images' public-store walk
        # would descend into it and --delete the documents, and the GCS→local
        # media rsync would copy them into the public tree.
        # default_acl: 'private' (owner-only) — override the global
        # GS_DEFAULT_ACL=authenticatedRead so a private blob isn't readable by
        # any authenticated Google principal that learns its path. The gated
        # download/thumbnail views (which stream via storage.open, never .url)
        # are the only intended readers.
        'OPTIONS': {'location': f'{_GS_LOCATION}-private', 'default_acl': 'private'},
    }


STORAGES = {
    'default': {
        'BACKEND': _MEDIA_BACKEND,
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
    'private': _private_storage_config(STORAGE_BACKEND),
}
# Collected static (served by WhiteNoise). Defaults under the project dir so a
# local `collectstatic` works on any machine (the old hardcoded /data/static is
# a read-only path off a laptop); env-overridable like MEDIA_ROOT. In the
# container, build-time collectstatic and runtime both use this default, so they
# stay consistent without extra config.
STATIC_ROOT = os.environ.get('STATIC_ROOT', BASE_DIR / 'staticfiles')
STATIC_URL = '/static/'

LOGIN_URL = 'art:curate:login'
LOGIN_REDIRECT_URL = 'art:curate:piece-list'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# FileSystemStorage (the 'local' backend) stores under MEDIA_ROOT and serves at
# MEDIA_URL; both are env-overridable so a self-host can point MEDIA_ROOT at its
# mounted NAS path (and keep dev/prod media separate the way GS_LOCATION does for
# GCS). Defaults under the project dir so a fresh local run (uploads, seed_demo)
# works without /data; the container sets it to /data/media explicitly. Ignored
# by the GCS backend.
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', BASE_DIR / 'media')
MEDIA_URL = os.environ.get('MEDIA_URL', '/media/')

# Image-upload limits (uploads are staff-only, but bound memory/disk). The byte
# cap is enforced in PieceForm/ArtistForm; MAX_IMAGE_PIXELS is applied to Pillow
# in art.apps so decoding a huge/bomb image raises instead of exhausting memory.
MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_IMAGE_PIXELS = 120_000_000  # ~120 MP: generous for high-res scans, blocks decompression bombs

# Staff document uploads (receipts, valuations, …) — bound size and restrict to
# PDFs + images. Enforced by validate_document_upload in the add view.
MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
ALLOWED_DOCUMENT_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic')

THUMBNAIL_ALIASES = {
    '': {
        'thumb': {'size': (400, 400), 'crop': False, 'sharpen': True},
    },
}

# Pinned to the same backend as STORAGES['default'] (see STORAGE_BACKEND):
# easy-thumbnails keeps its own storage setting, and the alias points it at
# STORAGES['default'] so thumbnails always land beside their source images.
THUMBNAIL_DEFAULT_STORAGE = _MEDIA_BACKEND
THUMBNAIL_DEFAULT_STORAGE_ALIAS = 'default'

# GCS-only settings, defined only for that backend (ignored by, and absent
# under, the local FileSystemStorage backend). Bucket/project are env-driven so
# any deployment points at its own GCS — supply them via the Fly env / a secret.
if STORAGE_BACKEND == 'gcs':
    # Required: fail closed (like SECRET_KEY / ALLOWED_HOSTS) rather than handing
    # django-storages a None bucket and surfacing a confusing error on first use.
    GS_BUCKET_NAME = os.environ.get('GS_BUCKET_NAME')
    if not GS_BUCKET_NAME:
        raise ImproperlyConfigured('GS_BUCKET_NAME is required when STORAGE_BACKEND=gcs.')
    # Optional: the google client infers the project from the service-account key
    # when this is unset.
    GS_PROJECT_ID = os.environ.get('GS_PROJECT_ID')
    GS_DEFAULT_ACL = 'authenticatedRead'
    GS_LOCATION = _GS_LOCATION

# Security: only require secure cookies / HSTS in production so local HTTP dev still works.
if PROD:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    # The in-container healthcheck curls http://localhost:8000/healthz/ (plain
    # HTTP), which would otherwise 301 to https; exempt it so it gets a real 200.
    SECURE_REDIRECT_EXEMPT = [r'^healthz/$']
    # Behind the Fly / Caddy reverse proxy the TCP peer is the proxy, so axes
    # would otherwise see every request as one IP (and lock the whole site out
    # on the first attack). Resolve the REAL client per edge instead — Fly's
    # Fly-Client-IP, or Caddy's right-most X-Forwarded-For hop (see
    # art.ratelimit.client_ip). INVARIANT: production runs behind exactly that
    # one trusted, client-appending proxy; if a CDN / extra hop is ever added in
    # front, revisit client_ip or the per-IP lockout could be spoofed.
    # Production-only: dev has no trusted proxy, so axes keeps its REMOTE_ADDR
    # default there (a spoofed header on a directly-reachable server is ignored).
    AXES_CLIENT_IP_CALLABLE = 'art.ratelimit.client_ip'
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # 30 days; bump once you're confident
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
X_FRAME_OPTIONS = 'DENY'

# Console logs: human-readable text in dev, structured JSON in production (so a
# log shipper / aggregator can parse them). Override with LOG_FORMAT=console|json.
LOG_FORMAT = os.environ.get('LOG_FORMAT') or ('json' if PROD else 'console')
if LOG_FORMAT not in ('console', 'json'):
    LOG_FORMAT = 'console'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '{levelname} {asctime} {name} {message}',
            'style': '{',
        },
        'json': {
            '()': 'artsite.jsonlog.JSONFormatter',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': LOG_FORMAT,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# --- Error tracking (Sentry) ---
# Initialised only when SENTRY_DSN is set, so dev and unconfigured deploys are a
# no-op (errors still go to the console logger above). sentry-sdk auto-enables its
# Django integration, capturing unhandled 500s. send_default_pii=False keeps user
# data/IPs out of events (cf. the public-media privacy posture); traces_sample_rate
# is 0 — error tracking only, no performance sampling. Set via a Fly secret /
# artsite.env: SENTRY_DSN=...
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=_ENVIRONMENT,
        send_default_pii=False,
        traces_sample_rate=0,
    )
