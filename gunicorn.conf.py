"""Gunicorn settings, read from the environment so each deployment tunes itself.

Loaded with `gunicorn -c gunicorn.conf.py artsite.wsgi` from the Dockerfile CMD
(Fly), the Podman quadlet, and the compose entrypoint — one source of truth for
worker sizing and recycling. Defaults suit a small box; raise GUNICORN_WORKERS on
a bigger one.
"""

import os


def _int(name, default):
    return int(os.environ.get(name, default))


# Fly and the Podman quadlet route over IPv6 ([::]); compose's default bridge
# network is IPv4, so it sets GUNICORN_BIND=0.0.0.0:8000.
bind = os.environ.get('GUNICORN_BIND', '[::]:8000')
workers = _int('GUNICORN_WORKERS', 2)
threads = _int('GUNICORN_THREADS', 4)

# Recycle each worker after this many requests (plus jitter) so a slow leak or a
# large image-decode buffer can't grow a worker's memory unbounded — the failure
# mode behind the earlier small-machine OOM. Set GUNICORN_MAX_REQUESTS=0 to disable.
max_requests = _int('GUNICORN_MAX_REQUESTS', 500)
max_requests_jitter = _int('GUNICORN_MAX_REQUESTS_JITTER', 50)

# Drain in-flight requests on SIGTERM (matches fly.toml kill_signal/kill_timeout).
graceful_timeout = _int('GUNICORN_GRACEFUL_TIMEOUT', 30)

accesslog = '-'
access_log_format = '%({x-forwarded-for}i)s %(t)s %(m)s %(U)s%(q)s %(s)s %(D)sus %(b)sb'
