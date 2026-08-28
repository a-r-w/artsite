"""Settings parsing — the env-driven ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS.

These reload the production settings module under a controlled environment, so
they assert the SHIPPED parsing logic rather than the test-suite's own settings.
Each test restores the module afterwards.
"""

import importlib
import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

import artsite.settings as settings_module

# Env vars that steer the host/origin parsing — cleared before each reload so an
# ambient value can't leak into a case.
_STEERED = (
    'ALLOWED_HOSTS',
    'CSRF_TRUSTED_ORIGINS',
    'ENVIRONMENT',
    'DJANGO_SECRET_KEY',
    'DATABASE_URL',
    'SENTRY_DSN',
    'LOG_FORMAT',
    # GCS config is env-driven; clear it so an ambient value can't leak into the
    # fail-closed cases. STORAGE_BACKEND is intentionally NOT steered — it stays
    # at the suite's 'local', and the gcs cases pass it as an explicit override.
    'GS_BUCKET_NAME',
    'GS_PROJECT_ID',
    'GS_LOCATION',
    'STATIC_ROOT',
    'MEDIA_ROOT',
    'PRIVATE_MEDIA_ROOT',
    'LANGUAGE_CODE',
    'TIME_ZONE',
)


class HostAllowlistTests(SimpleTestCase):
    def _reload(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in _STEERED}
        env.update(overrides)
        with mock.patch.dict(os.environ, env, clear=True):
            return importlib.reload(settings_module)

    def tearDown(self):
        # Restore the module to the ambient (dev/test) environment for other tests.
        importlib.reload(settings_module)

    def test_dev_defaults_to_localhost_only(self):
        s = self._reload()  # development, no ALLOWED_HOSTS set
        self.assertEqual(s.ALLOWED_HOSTS, ['localhost', '127.0.0.1'])
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, [])

    def test_env_hosts_are_appended_and_csrf_is_derived(self):
        s = self._reload(ALLOWED_HOSTS='example.com, www.example.com')
        self.assertEqual(s.ALLOWED_HOSTS, ['localhost', '127.0.0.1', 'example.com', 'www.example.com'])
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, ['https://example.com', 'https://www.example.com'])

    def test_ip_and_localhost_hosts_get_no_csrf_origin(self):
        s = self._reload(ALLOWED_HOSTS='203.0.113.5, localhost')
        self.assertIn('203.0.113.5', s.ALLOWED_HOSTS)
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, [])  # an IP / bare localhost isn't a domain

    def test_csrf_origins_can_be_overridden(self):
        s = self._reload(
            ALLOWED_HOSTS='example.com',
            CSRF_TRUSTED_ORIGINS='https://example.com, https://cdn.example.com',
        )
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, ['https://example.com', 'https://cdn.example.com'])

    def test_production_requires_allowed_hosts(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload(ENVIRONMENT='production', DJANGO_SECRET_KEY='x', DATABASE_URL='sqlite://')

    def test_production_with_hosts_keeps_localhost_for_the_healthcheck(self):
        s = self._reload(
            ENVIRONMENT='production',
            DJANGO_SECRET_KEY='x',
            DATABASE_URL='sqlite://',
            ALLOWED_HOSTS='example.com',
        )
        self.assertIn('example.com', s.ALLOWED_HOSTS)
        self.assertIn('localhost', s.ALLOWED_HOSTS)  # in-container healthcheck stays allowed
        self.assertEqual(s.CSRF_TRUSTED_ORIGINS, ['https://example.com'])

    def test_production_exempts_healthz_from_ssl_redirect(self):
        # Else the in-container HTTP healthcheck would get a 301, not a 200.
        s = self._reload(
            ENVIRONMENT='production',
            DJANGO_SECRET_KEY='x',
            DATABASE_URL='sqlite://',
            ALLOWED_HOSTS='example.com',
        )
        self.assertIn(r'^healthz/$', s.SECURE_REDIRECT_EXEMPT)

    def test_sentry_initialised_only_when_dsn_set(self):
        with mock.patch('sentry_sdk.init') as init:
            self._reload(SENTRY_DSN='https://k@example.invalid/1')
        init.assert_called_once()
        self.assertEqual(init.call_args.kwargs.get('dsn'), 'https://k@example.invalid/1')

    def test_sentry_not_initialised_without_dsn(self):
        with mock.patch('sentry_sdk.init') as init:
            self._reload()  # no SENTRY_DSN -> no-op
        init.assert_not_called()

    def test_log_format_is_text_in_dev_json_in_prod(self):
        dev = self._reload()
        self.assertEqual(dev.LOG_FORMAT, 'console')
        self.assertEqual(dev.LOGGING['handlers']['console']['formatter'], 'console')
        prod = self._reload(
            ENVIRONMENT='production',
            DJANGO_SECRET_KEY='x',
            DATABASE_URL='sqlite://',
            ALLOWED_HOSTS='example.com',
        )
        self.assertEqual(prod.LOG_FORMAT, 'json')
        self.assertEqual(prod.LOGGING['handlers']['console']['formatter'], 'json')

    def test_invalid_log_format_falls_back_to_console(self):
        self.assertEqual(self._reload(LOG_FORMAT='garbage').LOG_FORMAT, 'console')

    def test_gcs_backend_requires_a_bucket_name(self):
        # Fail closed: selecting gcs without GS_BUCKET_NAME must refuse to boot,
        # not hand django-storages a None bucket that errors later.
        with self.assertRaises(ImproperlyConfigured):
            self._reload(STORAGE_BACKEND='gcs')

    def test_gcs_backend_reads_bucket_and_project_from_env(self):
        s = self._reload(STORAGE_BACKEND='gcs', GS_BUCKET_NAME='friend-bucket', GS_PROJECT_ID='friend-proj')
        self.assertEqual(s.GS_BUCKET_NAME, 'friend-bucket')
        self.assertEqual(s.GS_PROJECT_ID, 'friend-proj')
        self.assertEqual(s.STORAGES['default']['BACKEND'], 'storages.backends.gcloud.GoogleCloudStorage')

    def test_gcs_location_prefix_is_overridable(self):
        s = self._reload(STORAGE_BACKEND='gcs', GS_BUCKET_NAME='b', GS_LOCATION='myart')
        self.assertEqual(s.GS_LOCATION, 'myart')

    def test_production_requires_database_url(self):
        # Fail closed: prod without DATABASE_URL must not silently use SQLite.
        with self.assertRaises(ImproperlyConfigured):
            self._reload(ENVIRONMENT='production', DJANGO_SECRET_KEY='x', ALLOWED_HOSTS='example.com')

    def test_dev_defaults_to_a_sqlite_file_when_database_url_unset(self):
        s = self._reload()  # development, no DATABASE_URL
        self.assertEqual(s.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')

    def test_static_root_defaults_under_the_project_and_is_overridable(self):
        self.assertTrue(str(self._reload().STATIC_ROOT).endswith('staticfiles'))
        self.assertEqual(str(self._reload(STATIC_ROOT='/tmp/static').STATIC_ROOT), '/tmp/static')

    def test_locale_defaults_to_us_and_is_overridable(self):
        s = self._reload()
        self.assertEqual((s.LANGUAGE_CODE, s.TIME_ZONE), ('en-us', 'UTC'))
        s2 = self._reload(LANGUAGE_CODE='en-gb', TIME_ZONE='Europe/London')
        self.assertEqual((s2.LANGUAGE_CODE, s2.TIME_ZONE), ('en-gb', 'Europe/London'))

    def test_media_roots_default_under_the_project_and_are_overridable(self):
        s = self._reload()  # bare local run: writable paths under the project, not /data
        self.assertTrue(str(s.MEDIA_ROOT).endswith('media'))
        self.assertTrue(str(s.PRIVATE_MEDIA_ROOT).endswith('private'))
        self.assertFalse(str(s.MEDIA_ROOT).startswith('/data'))
        self.assertEqual(str(self._reload(MEDIA_ROOT='/srv/nas/media').MEDIA_ROOT), '/srv/nas/media')
