"""Tests for `manage.py backfill_strip_image_metadata` — stripping metadata (and
dropping the leaked client filename) from images ALREADY in storage."""

import io

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import TestCase
from PIL import Image

from art.images import image_has_metadata

from .factories import make_artist, make_location, make_piece
from .test_images import exif_jpeg


class BackfillStripTests(TestCase):
    def _seed_legacy_image(self, instance, field_name, stem='holiday_2019'):
        """Write an EXIF-bearing file under the OLD <pk>-<stem>.jpg scheme and
        point the row at it via .update() — bypassing the upload-time strip, as a
        pre-feature row would be."""
        raw = exif_jpeg().read()
        name = f'{instance.pk}-{stem}.jpg'
        default_storage.save(name, ContentFile(raw))
        type(instance).objects.filter(pk=instance.pk).update(**{field_name: name})
        instance.refresh_from_db()
        return name

    def _stored_bytes(self, fieldfile):
        fieldfile.open('rb')
        try:
            return fieldfile.read()
        finally:
            fieldfile.close()

    def test_dry_run_changes_nothing(self):
        piece = make_piece(artist=make_artist(), location=make_location())
        legacy = self._seed_legacy_image(piece, 'image')
        call_command('backfill_strip_image_metadata')  # no --apply
        piece.refresh_from_db()
        self.assertEqual(piece.image.name, legacy)  # not renamed
        self.assertTrue(image_has_metadata(self._stored_bytes(piece.image)))  # not stripped

    def test_apply_strips_and_renames_legacy_image(self):
        piece = make_piece(artist=make_artist(), location=make_location())
        legacy = self._seed_legacy_image(piece, 'image', stem='Marthas_Vineyard_2019')
        self.assertTrue(image_has_metadata(self._stored_bytes(piece.image)))  # precondition

        call_command('backfill_strip_image_metadata', '--apply')
        piece.refresh_from_db()

        self.assertEqual(piece.image.name, f'{piece.pk}.jpg')  # renamed, stem dropped
        self.assertFalse(default_storage.exists(legacy))  # old object removed
        stored = self._stored_bytes(piece.image)
        self.assertEqual(list(Image.open(io.BytesIO(stored)).getexif().keys()), [])  # stripped
        self.assertNotIn(b'Marthas_Vineyard_2019', stored)

    def test_artist_portrait_is_also_swept(self):
        artist = make_artist()
        self._seed_legacy_image(artist, 'portrait')
        call_command('backfill_strip_image_metadata', '--apply')
        artist.refresh_from_db()
        self.assertEqual(artist.portrait.name, f'{artist.pk}.jpg')
        self.assertEqual(list(Image.open(io.BytesIO(self._stored_bytes(artist.portrait))).getexif().keys()), [])

    def test_idempotent_second_run_is_a_noop(self):
        piece = make_piece(artist=make_artist(), location=make_location())
        self._seed_legacy_image(piece, 'image')
        call_command('backfill_strip_image_metadata', '--apply')
        piece.refresh_from_db()
        before = self._stored_bytes(piece.image)
        name_before = piece.image.name

        call_command('backfill_strip_image_metadata', '--apply')  # again
        piece.refresh_from_db()
        self.assertEqual(piece.image.name, name_before)  # not re-renamed
        self.assertEqual(self._stored_bytes(piece.image), before)  # not re-encoded

    def test_already_clean_new_scheme_image_is_skipped(self):
        # A piece uploaded through the (stripping) upload path is already clean and
        # on the stem-less name, so the sweep leaves it byte-identical.
        piece = make_piece(artist=make_artist(), location=make_location())
        piece.image = exif_jpeg(name='whatever.jpg')
        piece.save()  # upload path strips + names <pk>.jpg
        before = self._stored_bytes(piece.image)
        name_before = piece.image.name
        call_command('backfill_strip_image_metadata', '--apply')
        piece.refresh_from_db()
        self.assertEqual(piece.image.name, name_before)
        self.assertEqual(self._stored_bytes(piece.image), before)
