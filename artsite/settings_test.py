"""Settings for the test suite.

Self-contained: SQLite instead of Postgres, and in-memory file storage
instead of Google Cloud Storage, so tests need no network, credentials, or
external services.

    python manage.py test --settings=artsite.settings_test
"""

import os

# Tests use the local/in-memory storage config (STORAGES is overridden to
# InMemoryStorage below). Pin STORAGE_BACKEND=local BEFORE importing the base
# settings — which read it at module load — so the gcs branch (now fail-closed
# without GS_BUCKET_NAME) doesn't run, and no author bucket leaks into the suite.
os.environ.setdefault('STORAGE_BACKEND', 'local')

from .settings import *  # noqa: E402,F401,F403

# In-memory SQLite — fast, disposable, no Mac Postgres dependency.
# ATOMIC_REQUESTS mirrors production so the suite exercises the same per-request
# transaction semantics.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'ATOMIC_REQUESTS': True,
    }
}

# Keep all file I/O in memory; never touch GCS during tests.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    'private': {'BACKEND': 'art.storage.PrivateInMemoryStorage'},
}
THUMBNAIL_DEFAULT_STORAGE = 'django.core.files.storage.InMemoryStorage'
THUMBNAIL_DEFAULT_STORAGE_ALIAS = 'default'

# Fast hashing — the suite creates lots of users.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

DEBUG = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']

# Login rate-limiting off by default so the wider suite (which logs in constantly
# via force_login) is unaffected; the dedicated lockout tests in test_ratelimit.py
# re-enable it per-test with @override_settings(AXES_ENABLED=True).
AXES_ENABLED = False

# Prod-only redirects must stay off for the test client even if ENVIRONMENT leaks in.
SECURE_SSL_REDIRECT = False

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Keep test output clean: the security suite deliberately triggers 403s /
# PermissionDenied, which Django would otherwise log as warnings.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'handlers': {'null': {'class': 'logging.NullHandler'}},
    'root': {'handlers': ['null'], 'level': 'CRITICAL'},
}
