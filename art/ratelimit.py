"""Client-IP resolution for django-axes login rate-limiting.

Wired in via ``AXES_CLIENT_IP_CALLABLE`` (production only — see settings.py),
where a single trusted reverse proxy always fronts the app.
"""

import ipaddress

from django.conf import settings


def _clean(entry):
    """Normalise one address to a bare IP string, or None if it isn't an IP.

    Strips an optional port and IPv6 brackets ("[2001:db8::1]:443" -> "2001:db8::1",
    "203.0.113.7:51000" -> "203.0.113.7") so an ephemeral source port can't
    fragment the lockout key, and rejects any non-IP token outright.
    """
    entry = entry.strip()
    if entry.startswith('['):  # [IPv6] or [IPv6]:port
        entry = entry[1:].split(']', 1)[0]
    elif entry.count(':') == 1:  # IPv4:port (a single colon, not a bare IPv6)
        entry = entry.rsplit(':', 1)[0]
    try:
        return str(ipaddress.ip_address(entry))
    except ValueError:
        return None


def client_ip(request):
    """The real client IP to key a login lockout on — resolved per deployment edge.

    Fly.io (``STORAGE_BACKEND='gcs'``): Fly Proxy is the outermost edge and records
    the real client in ``Fly-Client-IP``. Its ``X-Forwarded-For`` appends the app's
    OWN address as the right-most hop (a constant), so XFF must NOT be used there —
    doing so would collapse every request to one IP and let anyone lock the curator
    out by username alone.

    Self-host Caddy (``STORAGE_BACKEND='local'``): Caddy is the single appending
    proxy, so the real client is the RIGHT-most ``X-Forwarded-For`` entry — a
    client-supplied prefix sits to its left and so can't spoof past the lockout.

    Either way, fall back to ``REMOTE_ADDR`` when the trusted header is absent. Only
    installed in production, where a trusted proxy is guaranteed in front; local dev
    keeps axes' ``REMOTE_ADDR`` default, so a spoofed header is never honoured.
    """
    if settings.STORAGE_BACKEND == 'gcs':
        fly = _clean(request.META.get('HTTP_FLY_CLIENT_IP', ''))
        return fly or request.META.get('REMOTE_ADDR')

    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    for hop in reversed(forwarded.split(',')):
        ip = _clean(hop)
        if ip:
            return ip
    return request.META.get('REMOTE_ADDR')
