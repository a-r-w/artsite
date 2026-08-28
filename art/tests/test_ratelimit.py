"""Login rate-limiting (django-axes).

Brute-force lockout on the curate login (/curate/login/) and the Django admin
(/admin/login/). Axes is disabled in settings_test by default — the rest of the
suite authenticates via force_login, which bypasses the backends — so each test
here re-enables it explicitly and resets axes state in setUp.
"""

import importlib
import os
from datetime import timedelta
from unittest import mock

from axes.utils import reset
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from art.ratelimit import _clean

from .factories import make_staff

# Small limit keeps the tests fast; lock on the (username, IP) pair as in prod.
AXES = {
    'AXES_ENABLED': True,
    'AXES_FAILURE_LIMIT': 3,
    'AXES_RESET_ON_SUCCESS': True,
    'AXES_LOCKOUT_PARAMETERS': [['username', 'ip_address']],
}

PASSWORD = 's3cret-pw'

# Axes returns 429 Too Many Requests for a locked-out attempt (its default).
LOCKED = 429


@override_settings(**AXES)
class LoginRateLimitTests(TestCase):
    def setUp(self):
        reset()  # no AccessAttempt carryover between tests
        make_staff(username='curator', password=PASSWORD)
        self.url = reverse('art:curate:login')

    def _fail(self):
        return self.client.post(self.url, {'username': 'curator', 'password': 'wrong'})

    def _succeed(self):
        return self.client.post(self.url, {'username': 'curator', 'password': PASSWORD})

    def test_lockout_after_failure_limit(self):
        # FAILURE_LIMIT=3: the first two attempts re-render the form (200); the
        # third reaches the limit and is locked out (429), as is everything after.
        self.assertEqual(self._fail().status_code, 200)
        self.assertEqual(self._fail().status_code, 200)
        locked = self._fail()
        self.assertEqual(locked.status_code, LOCKED)
        self.assertContains(locked, 'Too many failed sign-in attempts', status_code=LOCKED)

    def test_lockout_blocks_even_the_correct_password(self):
        for _ in range(3):
            self._fail()
        # The right password now — but the source is locked, so it's still denied.
        resp = self._succeed()
        self.assertEqual(resp.status_code, LOCKED)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_below_limit_then_correct_password_logs_in(self):
        self._fail()
        self._fail()  # 2 < 3 — no lockout
        resp = self._succeed()
        self.assertEqual(resp.status_code, 302)  # redirected into the admin
        self.assertIn('_auth_user_id', self.client.session)

    def test_success_resets_the_failure_tally(self):
        # AXES_RESET_ON_SUCCESS: 2 fails (< limit), then a good login clears the
        # tally, so two more typos don't lock. Without the reset the first of
        # those would be the cumulative 3rd failure and return 429.
        self._fail()
        self._fail()
        self.assertEqual(self._succeed().status_code, 302)
        self.client.logout()  # else the next POST 302s away before axes sees it
        self.assertEqual(self._fail().status_code, 200)
        self.assertEqual(self._fail().status_code, 200)

    def test_admin_login_is_also_rate_limited(self):
        admin_login = reverse('admin:login')
        payload = {'username': 'curator', 'password': 'wrong', 'next': '/admin/'}
        for _ in range(2):
            self.assertNotEqual(self.client.post(admin_login, payload).status_code, LOCKED)
        locked = self.client.post(admin_login, payload)
        self.assertEqual(locked.status_code, LOCKED)
        # The admin edge serves the same lockout page as the curate login.
        self.assertContains(locked, 'Too many failed sign-in attempts', status_code=LOCKED)


@override_settings(
    AXES_ENABLED=True,
    AXES_FAILURE_LIMIT=3,
    AXES_LOCKOUT_PARAMETERS=['ip_address'],  # isolate IP behaviour
    AXES_CLIENT_IP_CALLABLE='art.ratelimit.client_ip',  # the prod proxy resolver
    STORAGE_BACKEND='local',  # self-host Caddy edge: real client = right-most XFF
)
class CaddyClientIpTests(TestCase):
    """Self-host (Caddy) edge: Caddy appends the real client as the LAST
    X-Forwarded-For hop, so the lockout keys on the right-most entry and a
    client-supplied prefix can't spoof around it."""

    REAL_A = '52.10.20.30'
    REAL_B = '52.10.20.40'
    REAL_V6 = '2001:db8::1'
    REAL_V6_B = '2001:db8::2'

    def setUp(self):
        reset()
        make_staff(username='curator', password=PASSWORD)
        self.url = reverse('art:curate:login')

    def _fail(self, xff, **headers):
        return self.client.post(
            self.url,
            {'username': 'curator', 'password': 'wrong'},
            HTTP_X_FORWARDED_FOR=xff,
            **headers,
        )

    def test_lockout_keys_on_the_rightmost_real_client(self):
        for _ in range(3):
            self._fail(f'11.11.11.11, {self.REAL_A}')
        # Same real client, DIFFERENT spoofed prefix → still locked, proving the
        # right-most (proxy-appended) entry is the key and the prefix is ignored.
        self.assertEqual(self._fail(f'9.9.9.9, {self.REAL_A}').status_code, LOCKED)

    def test_a_different_real_client_is_not_locked(self):
        for _ in range(3):
            self._fail(f'11.11.11.11, {self.REAL_A}')
        # A different right-most real client is unaffected — a normal failed login.
        self.assertEqual(self._fail(f'11.11.11.11, {self.REAL_B}').status_code, 200)
        # ...and the original client is STILL locked: proves tracking is live and
        # per-IP, not just that every failed login happens to return 200.
        self.assertEqual(self._fail(f'9.9.9.9, {self.REAL_A}').status_code, LOCKED)

    def test_ephemeral_port_does_not_fragment_the_key(self):
        # A proxy that tacks a source port onto the hop must not splinter the key.
        # The spoofed prefix forces the port-bearing entry to be the right-most
        # hop, so if the port weren't stripped the key would shift to the prefix
        # (and not lock) — this pins the strip path, not a shared REMOTE_ADDR.
        for _ in range(3):
            self._fail(f'8.8.8.8, {self.REAL_A}:40001')
        self.assertEqual(self._fail(f'9.9.9.9, {self.REAL_A}:40002').status_code, LOCKED)

    def test_lockout_keys_on_rightmost_ipv6_client(self):
        for _ in range(3):
            self._fail(f'11.11.11.11, {self.REAL_V6}')
        self.assertEqual(self._fail(f'9.9.9.9, {self.REAL_V6}').status_code, LOCKED)
        # a different IPv6 client is unaffected
        self.assertEqual(self._fail(f'11.11.11.11, {self.REAL_V6_B}').status_code, 200)

    def test_fly_client_ip_header_is_ignored_on_caddy(self):
        # Caddy doesn't strip a client-supplied Fly-Client-IP; the local branch
        # must never read it, so a spoofed value can't move the key off the XFF hop.
        for _ in range(3):
            self._fail(f'9.9.9.9, {self.REAL_A}', HTTP_FLY_CLIENT_IP='1.2.3.4')
        self.assertEqual(self._fail(f'8.8.8.8, {self.REAL_A}', HTTP_FLY_CLIENT_IP='1.2.3.4').status_code, LOCKED)
        self.assertEqual(self._fail(f'9.9.9.9, {self.REAL_B}', HTTP_FLY_CLIENT_IP='1.2.3.4').status_code, 200)


@override_settings(
    AXES_ENABLED=True,
    AXES_FAILURE_LIMIT=3,
    AXES_LOCKOUT_PARAMETERS=['ip_address'],
    AXES_CLIENT_IP_CALLABLE='art.ratelimit.client_ip',
    STORAGE_BACKEND='gcs',  # Fly edge: real client = Fly-Client-IP, NOT XFF
)
class FlyClientIpTests(TestCase):
    """Fly edge: the right-most X-Forwarded-For entry is the app's OWN IP, so the
    lockout must key on Fly-Client-IP instead — otherwise it would collapse to one
    IP for everyone and let anyone lock the curator out."""

    def setUp(self):
        reset()
        make_staff(username='curator', password=PASSWORD)
        self.url = reverse('art:curate:login')

    def _fail(self, **headers):
        return self.client.post(self.url, {'username': 'curator', 'password': 'wrong'}, **headers)

    def test_lockout_keys_on_fly_client_ip(self):
        for _ in range(3):
            self._fail(HTTP_FLY_CLIENT_IP='52.10.20.30', HTTP_X_FORWARDED_FOR='52.10.20.30, 66.66.66.66')
        # Same Fly client, different XFF/app-IP → still locked (XFF is ignored).
        self.assertEqual(
            self._fail(HTTP_FLY_CLIENT_IP='52.10.20.30', HTTP_X_FORWARDED_FOR='52.10.20.30, 77.77.77.77').status_code,
            LOCKED,
        )

    def test_a_different_fly_client_is_not_locked(self):
        for _ in range(3):
            self._fail(HTTP_FLY_CLIENT_IP='52.10.20.30')
        self.assertEqual(self._fail(HTTP_FLY_CLIENT_IP='52.10.20.40').status_code, 200)
        self.assertEqual(self._fail(HTTP_FLY_CLIENT_IP='52.10.20.30').status_code, LOCKED)

    def test_lockout_keys_on_ipv6_fly_client_ip(self):
        for _ in range(3):
            self._fail(HTTP_FLY_CLIENT_IP='2001:db8::1')
        self.assertEqual(self._fail(HTTP_FLY_CLIENT_IP='2001:db8::2').status_code, 200)
        self.assertEqual(self._fail(HTTP_FLY_CLIENT_IP='2001:db8::1').status_code, LOCKED)

    def test_xff_alone_cannot_lock_on_fly(self):
        # Rotating Fly-Client-IP with a constant XFF must NOT accumulate a lockout
        # — i.e. axes keys on Fly-Client-IP here, never the (constant) XFF tail.
        last = None
        for ip in ('1.1.1.1', '2.2.2.2', '3.3.3.3'):
            last = self._fail(HTTP_FLY_CLIENT_IP=ip, HTTP_X_FORWARDED_FOR='9.9.9.9, 52.10.20.30')
        self.assertEqual(last.status_code, 200)


class ClientIpUnitTests(SimpleTestCase):
    """Direct checks on the resolver's normalisation, independent of axes — these
    pin the IPv6 / port-stripping / reject-garbage branches that the integration
    tests above could otherwise satisfy via the shared REMOTE_ADDR fallback."""

    def test_clean_passes_through_bare_addresses(self):
        self.assertEqual(_clean('203.0.113.7'), '203.0.113.7')
        self.assertEqual(_clean('2001:db8::1'), '2001:db8::1')
        self.assertEqual(_clean(' 203.0.113.7 '), '203.0.113.7')

    def test_clean_strips_ports_and_brackets(self):
        self.assertEqual(_clean('203.0.113.7:51000'), '203.0.113.7')
        self.assertEqual(_clean('[2001:db8::1]:443'), '2001:db8::1')
        self.assertEqual(_clean('[2001:db8::1]'), '2001:db8::1')

    def test_clean_rejects_non_ips(self):
        self.assertIsNone(_clean('garbage'))
        self.assertIsNone(_clean(''))
        self.assertIsNone(_clean('   '))
        self.assertIsNone(_clean('1.2.3.4, 5.6.7.8'))  # a comma token is not one IP


class ShippedConfigTests(SimpleTestCase):
    """Pin the security-relevant axes wiring in the SHIPPED settings. The tests
    above use @override_settings, which would otherwise mask drift in production
    config (e.g. a weaker lockout key, a reordered backend, an unwired resolver).
    Mirrors the repo's test-hardening convention."""

    def setUp(self):
        from artsite import settings as prod

        self.prod = prod

    def test_lockout_keys_on_username_and_ip(self):
        self.assertEqual(self.prod.AXES_LOCKOUT_PARAMETERS, [['username', 'ip_address']])

    def test_axes_backend_is_first_and_modelbackend_present(self):
        self.assertEqual(self.prod.AUTHENTICATION_BACKENDS[0], 'axes.backends.AxesStandaloneBackend')
        self.assertIn('django.contrib.auth.backends.ModelBackend', self.prod.AUTHENTICATION_BACKENDS)

    def test_axes_middleware_is_last(self):
        self.assertEqual(self.prod.MIDDLEWARE[-1], 'axes.middleware.AxesMiddleware')

    def test_cooloff_window_is_fixed(self):
        # Attempts during a lockout must not roll the window forward.
        self.assertFalse(self.prod.AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT)

    def test_lockout_template_states_the_cooloff_minutes(self):
        # The lockout page hard-codes the duration; keep it tied to the setting so
        # changing AXES_COOLOFF_TIME forces the copy to be updated. Anchored to the
        # actual sentence so a stray digit elsewhere can't satisfy it.
        minutes = int(self.prod.AXES_COOLOFF_TIME / timedelta(minutes=1))
        html = render_to_string('curate/lockout.html', {'site_name': 'Test'})
        self.assertIn(f'about {minutes}&nbsp;minutes', html)

    def test_prod_wires_the_client_ip_resolver(self):
        # AXES_CLIENT_IP_CALLABLE lives in the PROD-only block, so it can't be read
        # from the dev module. Reload settings under ENVIRONMENT=production to prove
        # the per-edge resolver is actually wired (without it, axes falls back to
        # REMOTE_ADDR = the proxy IP, collapsing every client to one lockout key).
        with mock.patch.dict(
            os.environ,
            {
                'ENVIRONMENT': 'production',
                'DJANGO_SECRET_KEY': 'x',
                'DATABASE_URL': 'sqlite://',
                'ALLOWED_HOSTS': 'example.com',  # production refuses to start without it
            },
        ):
            prod = importlib.reload(self.prod)
        # Assert + restore OUTSIDE the patch, so the restoring reload runs under
        # the normal (dev) environment and prod state can't leak to other tests.
        try:
            self.assertEqual(prod.AXES_CLIENT_IP_CALLABLE, 'art.ratelimit.client_ip')
        finally:
            importlib.reload(self.prod)
