"""Tests for the verify_media management command — the pre-cutover check that
every database-referenced image is actually present in storage."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .factories import make_artist, make_document, make_piece, tiny_png


class VerifyMediaTests(TestCase):
    def test_passes_when_every_referenced_file_is_present(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        out = StringIO()
        call_command('verify_media', stdout=out)
        self.assertIn('All referenced images are present', out.getvalue())

    def test_fails_when_a_referenced_file_is_missing(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        piece.image.storage.delete(piece.image.name)  # row still references it; file gone
        with self.assertRaises(CommandError):
            call_command('verify_media', stdout=StringIO())

    def test_missing_file_is_reported_with_record_context(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        name = piece.image.name
        piece.image.storage.delete(name)
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_media', stdout=out)
        text = out.getvalue()
        self.assertIn(name, text)
        self.assertIn('art.Piece', text)
        self.assertIn(str(piece.pk), text)  # traceable back to the exact row

    def test_covers_artist_portrait_not_just_piece_image(self):
        # Introspection (not a hardcoded field list) keeps every image-bearing
        # model in scope, matching cleanup_orphan_images.
        artist = make_artist(name='Verified')
        artist.portrait = tiny_png()
        artist.save()
        artist.portrait.storage.delete(artist.portrait.name)
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_media', stdout=out)
        self.assertIn('art.Artist', out.getvalue())

    def test_private_storage_documents_are_not_checked(self):
        # A PieceDocument lives in the private store, not default_storage; verify
        # must skip it (else it would wrongly report every document as missing).
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        make_document(piece)
        out = StringIO()
        call_command('verify_media', stdout=out)  # must not raise / flag the document
        self.assertIn('All referenced images are present', out.getvalue())

    def test_succeeds_when_nothing_references_a_file(self):
        # Unlike cleanup, an empty referenced set is not dangerous — there is
        # simply nothing to verify, so it passes rather than refusing.
        make_piece()  # no image
        out = StringIO()
        call_command('verify_media', stdout=out)
        self.assertIn('referenced by the database: 0', out.getvalue())
        self.assertIn('All referenced images are present', out.getvalue())
