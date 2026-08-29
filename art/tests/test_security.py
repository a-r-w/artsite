"""Negative tests for security properties of the curate admin.

The curate gate is `is_active AND is_staff` (StaffRequiredMixin). These tests
assert the gate holds for every actor that should be denied, and — crucially —
that denied requests cause no state change.
"""

import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from art.models import Artist, Medium, Piece, PieceDocument
from art.views import StaffRequiredMixin

from .factories import (
    make_artist,
    make_document,
    make_location,
    make_medium,
    make_piece,
    make_staff,
    make_superuser,
    make_user,
    tiny_png,
)


class CurateUrlMatrix:
    """Build the set of staff-only curate URLs once per test, given fixtures."""

    def build_urls(self):
        artist = make_artist()
        medium = make_medium()
        location = make_location()
        piece = make_piece(artist=artist, location=location, medium=medium)
        document = make_document(piece, name='photo.png', content=tiny_png().read())
        document.generate_thumbnail()  # so the thumb URL is a real 200 for staff
        r = reverse
        return [
            r('art:curate:settings'),
            r('art:curate:piece-list'),
            r('art:curate:piece-add'),
            r('art:curate:quick-add'),
            r('art:curate:piece-edit', kwargs={'slug': piece.slug}),
            r('art:curate:piece-delete', kwargs={'slug': piece.slug}),
            r('art:curate:document-download', kwargs={'slug': piece.slug, 'pk': document.pk}),
            r('art:curate:document-thumb', kwargs={'slug': piece.slug, 'pk': document.pk}),
            r('art:curate:artist-inline-add'),
            r('art:curate:medium-inline-add'),
            r('art:curate:location-inline-add'),
            r('art:curate:artist-list'),
            r('art:curate:artist-add'),
            r('art:curate:artist-edit', kwargs={'pk': artist.pk}),
            r('art:curate:artist-delete', kwargs={'pk': artist.pk}),
            r('art:curate:medium-list'),
            r('art:curate:medium-add'),
            r('art:curate:medium-edit', kwargs={'pk': medium.pk}),
            r('art:curate:medium-delete', kwargs={'pk': medium.pk}),
            r('art:curate:location-list'),
            r('art:curate:location-add'),
            r('art:curate:location-edit', kwargs={'pk': location.pk}),
            r('art:curate:location-delete', kwargs={'pk': location.pk}),
        ]


class GateBlocksUnauthorisedTests(CurateUrlMatrix, TestCase):
    """AccessMixin's contract: anonymous users are redirected to login, while
    *authenticated* users who fail the test get a hard 403 (no login bounce)."""

    def _assert_all_redirect_to_login(self, client):
        for url in self.build_urls():
            with self.subTest(url=url):
                resp = client.get(url)
                self.assertEqual(resp.status_code, 302, url)
                self.assertIn('/curate/login/', resp.url, url)

    def test_anonymous_is_redirected_to_login(self):
        self._assert_all_redirect_to_login(Client())

    def test_authenticated_non_staff_gets_403(self):
        client = Client()
        client.force_login(make_user())
        for url in self.build_urls():
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 403, url)

    def test_inactive_staff_treated_as_anonymous(self):
        """is_staff is not enough — is_active is also required. ModelBackend
        rejects inactive accounts, so the session resolves to AnonymousUser
        and the request is redirected to login rather than 403'd."""
        client = Client()
        client.force_login(make_staff(username='suspended', is_active=False))
        self._assert_all_redirect_to_login(client)

    def test_inactive_staff_fails_the_mixin_gate_directly(self):
        """Backend-independent check of the gate's is_active half. The test
        above exercises ModelBackend (which drops the inactive session to
        AnonymousUser *before* the mixin runs), so it can't prove the mixin's
        own `is_active and …` term exists. This calls test_func() directly: it
        fails if that term is dropped, leaving only `is_staff`."""
        request = RequestFactory().get('/curate/')
        request.user = make_staff(username='suspended-direct', is_active=False)
        view = StaffRequiredMixin()
        view.request = request
        self.assertFalse(view.test_func())

    def test_staff_is_allowed(self):
        client = Client()
        client.force_login(make_staff())
        for url in self.build_urls():
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200, url)


class DeniedRequestsDoNotMutateTests(TestCase):
    """A blocked POST must be intercepted before the write happens."""

    def test_anonymous_cannot_delete_piece(self):
        piece = make_piece()
        resp = self.client.post(reverse('art:curate:piece-delete', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Piece.objects.filter(pk=piece.pk).exists())

    def test_non_staff_cannot_delete_artist(self):
        artist = make_artist()
        self.client.force_login(make_user())
        resp = self.client.post(reverse('art:curate:artist-delete', kwargs={'pk': artist.pk}))
        self.assertEqual(resp.status_code, 403)  # authenticated but unauthorised
        self.assertTrue(Artist.objects.filter(pk=artist.pk).exists())

    def test_anonymous_cannot_create_via_inline_endpoint(self):
        resp = self.client.post(reverse('art:curate:medium-inline-add'), {'description': 'Sneaky'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Medium.objects.filter(description='Sneaky').exists())

    def test_anonymous_cannot_bulk_create_pieces_via_quick_add(self):
        # Quick add is the widest write surface in the admin — an unauthenticated
        # POST could otherwise mint pieces and push files into public storage.
        resp = self.client.post(reverse('art:curate:quick-add'), {'photos': [tiny_png()]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/curate/login/', resp.url)
        self.assertEqual(Piece.objects.count(), 0)

    def test_non_staff_cannot_bulk_create_pieces_via_quick_add(self):
        self.client.force_login(make_user())
        resp = self.client.post(reverse('art:curate:quick-add'), {'photos': [tiny_png()]})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Piece.objects.count(), 0)

    def test_anonymous_cannot_add_documents(self):
        piece = make_piece()
        upload = SimpleUploadedFile('x.pdf', b'%PDF', content_type='application/pdf')
        resp = self.client.post(
            reverse('art:curate:document-add', kwargs={'slug': piece.slug}), {'documents': [upload]}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(piece.documents.count(), 0)

    def test_non_staff_cannot_delete_document(self):
        piece = make_piece()
        doc = make_document(piece)
        self.client.force_login(make_user())
        resp = self.client.post(reverse('art:curate:document-delete', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(PieceDocument.objects.filter(pk=doc.pk).exists())

    def test_non_staff_cannot_add_documents(self):
        piece = make_piece()
        self.client.force_login(make_user())
        upload = SimpleUploadedFile('x.pdf', b'%PDF', content_type='application/pdf')
        resp = self.client.post(
            reverse('art:curate:document-add', kwargs={'slug': piece.slug}), {'documents': [upload]}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(piece.documents.count(), 0)

    def test_anonymous_cannot_delete_document(self):
        piece = make_piece()
        doc = make_document(piece)
        resp = self.client.post(reverse('art:curate:document-delete', kwargs={'slug': piece.slug, 'pk': doc.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(PieceDocument.objects.filter(pk=doc.pk).exists())

    def test_anonymous_cannot_create_piece(self):
        artist = make_artist()
        location = make_location()
        resp = self.client.post(
            reverse('art:curate:piece-add'),
            {
                'title': 'Injected',
                'artist': str(artist.id),
                'location': str(location.id),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Piece.objects.filter(title='Injected').exists())


class CsrfProtectionTests(TestCase):
    """CSRF enforcement is on for unsafe methods (test client opts in here)."""

    def test_post_without_csrf_token_is_forbidden(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(make_staff())
        resp = client.post(reverse('art:curate:medium-add'), {'description': 'X'})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Medium.objects.exists())


class AdminGateTests(TestCase):
    def test_django_admin_requires_login(self):
        resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/login/', resp.url)

    def test_superuser_reaches_admin(self):
        self.client.force_login(make_superuser())
        self.assertEqual(self.client.get('/admin/').status_code, 200)


class ContentSecurityPolicyTests(TestCase):
    """CSP locks scripts to same-origin + a per-request nonce; that nonce must
    reach the inline scripts so they still execute under the policy."""

    def test_policy_header_is_present_and_strict(self):
        csp = self.client.get(reverse('art:index'))['Content-Security-Policy']
        for directive in (
            "default-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
        ):
            self.assertIn(directive, csp)

    def test_inline_scripts_carry_the_header_nonce(self):
        resp = self.client.get(reverse('art:index'))
        match = re.search(r"script-src 'self' 'nonce-([\w-]+)'", resp['Content-Security-Policy'])
        self.assertIsNotNone(match, resp['Content-Security-Policy'])
        # The pre-paint theme-init inline script must carry that exact nonce, or
        # a strict script-src would block it (and the page would flash the wrong
        # theme). This couples the header and the markup so neither drifts.
        self.assertContains(resp, f'<script nonce="{match.group(1)}">')

    def test_nonce_is_fresh_each_request(self):
        first = self.client.get(reverse('art:index'))['Content-Security-Policy']
        second = self.client.get(reverse('art:index'))['Content-Security-Policy']
        self.assertNotEqual(first, second)
