"""Smoke tests for the Django admin registration (art/admin.py)."""

from unittest import mock

from django.contrib import admin as django_admin
from django.test import TestCase
from django.urls import reverse

from art.admin import PieceAdmin
from art.models import Piece

from .factories import make_piece, make_superuser


class AdminSmokeTests(TestCase):
    def setUp(self):
        self.client.force_login(make_superuser())

    def test_piece_changelist_loads(self):
        make_piece(title='Catalogued')
        resp = self.client.get(reverse('admin:art_piece_changelist'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Catalogued')

    def test_piece_change_form_loads(self):
        piece = make_piece()
        resp = self.client.get(reverse('admin:art_piece_change', args=[piece.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_lookup_changelists_load(self):
        for model in ('artist', 'medium', 'location'):
            with self.subTest(model=model):
                resp = self.client.get(reverse(f'admin:art_{model}_changelist'))
                self.assertEqual(resp.status_code, 200)


class PieceAdminImageTagTests(TestCase):
    def setUp(self):
        self.piece_admin = PieceAdmin(Piece, django_admin.site)

    def test_placeholder_without_image(self):
        self.assertEqual(self.piece_admin.admin_image_tag(make_piece()), 'no image')

    def test_renders_img_when_image_present(self):
        piece = make_piece()
        piece.image = 'frida.jpg'  # truthy FieldFile name; thumbnailer is mocked
        with mock.patch('art.admin.get_thumbnailer') as get_thumb:
            get_thumb.return_value.__getitem__.return_value.url = '/t/frida.jpg'
            html = self.piece_admin.admin_image_tag(piece)
        self.assertIn('<img', html)
        self.assertIn('/t/frida.jpg', html)
