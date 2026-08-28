"""Favicons and the web app manifest.

The manifest is served by a view (not a static file) so its name follows the
curator-editable SiteSettings site name. The <head> of both the public and
curate templates links the icon set + manifest.
"""

import json

from django.test import TestCase
from django.urls import reverse

from art.models import SiteSettings

from .factories import make_staff


class ManifestViewTests(TestCase):
    def test_served_as_manifest_json(self):
        resp = self.client.get(reverse('art:manifest'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/manifest+json')

    def test_body_is_valid_json_with_expected_shape(self):
        data = json.loads(self.client.get(reverse('art:manifest')).content)
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['start_url'], '/')
        self.assertEqual({icon['sizes'] for icon in data['icons']}, {'192x192', '512x512'})

    def test_name_follows_site_settings(self):
        SiteSettings.objects.create(site_name='Gertrude’s "Wing" & Co')
        data = json.loads(self.client.get(reverse('art:manifest')).content)
        # The curator's name (with quotes/ampersand) round-trips through the JSON.
        self.assertEqual(data['name'], 'Gertrude’s "Wing" & Co')
        self.assertEqual(data['short_name'], 'Gertrude’s "Wing" & Co')


class FaviconLinkTests(TestCase):
    def test_public_head_links_icons_and_manifest(self):
        resp = self.client.get(reverse('art:index'))
        self.assertContains(resp, 'rel="manifest"')
        self.assertContains(resp, 'rel="apple-touch-icon"')
        self.assertContains(resp, 'favicon.svg')

    def test_curate_head_links_icons(self):
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:curate:piece-list'))
        self.assertContains(resp, 'rel="manifest"')
        self.assertContains(resp, 'apple-touch-icon')
