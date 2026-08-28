"""Tests for the cleanup_orphan_images management command.

Files are written straight to the default storage with controlled names (rather
than generating real thumbnails) so the command's classification and deletion
logic is exercised precisely, independent of the easy-thumbnails/test-storage
plumbing.
"""

import uuid
from io import StringIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from art.management.commands.cleanup_orphan_images import Command

from .factories import make_artist, make_document, make_piece, tiny_png


def _save(name, data=b'x'):
    """Write a file to the default storage; return its actual stored name."""
    return default_storage.save(name, ContentFile(data))


class CleanupOrphanImagesTests(TestCase):
    def _live_piece_with_thumbnail(self):
        """A piece with a stored image, plus a file named like one of its
        thumbnails. Returns (source_name, thumbnail_name)."""
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        source = piece.image.name
        thumb = _save(f'{source}.400x400_q85_sharpen.jpg', b'live-thumb')
        return source, thumb

    def test_dry_run_lists_orphans_and_deletes_nothing(self):
        source, live_thumb = self._live_piece_with_thumbnail()
        dead = f'{uuid.uuid4()}-gone.png'
        orphan = _save(dead, b'orphan')
        orphan_thumb = _save(f'{dead}.400x400_q85_sharpen.jpg', b'orphan-thumb')

        out = StringIO()
        call_command('cleanup_orphan_images', stdout=out)
        text = out.getvalue()

        self.assertIn(orphan, text)
        self.assertIn(orphan_thumb, text)
        self.assertNotIn(live_thumb, text)  # a thumbnail of a live source is not an orphan
        for name in (source, live_thumb, orphan, orphan_thumb):  # dry run: nothing removed
            self.assertTrue(default_storage.exists(name), name)

    def test_delete_removes_orphans_but_keeps_live_files(self):
        source, live_thumb = self._live_piece_with_thumbnail()
        dead = f'{uuid.uuid4()}-gone.png'
        orphan = _save(dead, b'orphan')
        orphan_thumb = _save(f'{dead}.400x400_q85_sharpen.jpg', b'orphan-thumb')

        call_command('cleanup_orphan_images', delete=True, no_input=True, min_age_days=0, stdout=StringIO())

        self.assertFalse(default_storage.exists(orphan))
        self.assertFalse(default_storage.exists(orphan_thumb))
        self.assertTrue(default_storage.exists(source))  # in use
        self.assertTrue(default_storage.exists(live_thumb))  # thumbnail of a live source

    def test_refuses_to_run_when_db_references_no_images(self):
        orphan = _save(f'{uuid.uuid4()}-lonely.png')
        with self.assertRaises(CommandError):
            call_command('cleanup_orphan_images', delete=True, no_input=True, min_age_days=0, stdout=StringIO())
        self.assertTrue(default_storage.exists(orphan))  # guard fired before any deletion

    def test_allow_empty_db_override_permits_deletion(self):
        orphan = _save(f'{uuid.uuid4()}-lonely.png')
        call_command(
            'cleanup_orphan_images',
            delete=True,
            no_input=True,
            min_age_days=0,
            allow_empty_db=True,
            stdout=StringIO(),
        )
        self.assertFalse(default_storage.exists(orphan))

    def test_recent_files_are_kept_by_the_age_guard(self):
        self._live_piece_with_thumbnail()  # satisfy the empty-db guard
        orphan = _save(f'{uuid.uuid4()}-fresh.png')
        # A just-written file is younger than the 1-day guard, so it's kept.
        call_command('cleanup_orphan_images', delete=True, no_input=True, min_age_days=1, stdout=StringIO())
        self.assertTrue(default_storage.exists(orphan))

    def test_in_use_set_covers_every_art_image_field(self):
        # Introspection (not a hardcoded field list) is what keeps a future
        # image-bearing model from having its files mistaken for orphans.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        artist = make_artist(name='Portraitist')
        artist.portrait = tiny_png()
        artist.save()
        sources = Command._in_use_sources()
        self.assertIn(piece.image.name, sources)
        self.assertIn(artist.portrait.name, sources)

    def test_private_documents_are_excluded_from_the_in_use_set(self):
        # The in-use set protects files during the public-store walk; a piece's
        # private documents live in another store and must not be considered.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        doc = make_document(piece)
        sources = Command._in_use_sources()
        self.assertIn(piece.image.name, sources)  # public image counted
        self.assertNotIn(doc.file.name, sources)  # private document excluded

    def test_negative_min_age_is_rejected(self):
        # A live image so the empty-db guard can't be what raises; then the
        # negative-age guard is the only thing that can, and we pin its message.
        self._live_piece_with_thumbnail()
        with self.assertRaises(CommandError) as cm:
            call_command('cleanup_orphan_images', delete=True, no_input=True, min_age_days=-1, stdout=StringIO())
        self.assertIn('min-age', str(cm.exception))

    @override_settings(THUMBNAIL_NAMER='easy_thumbnails.namers.hashed')
    def test_refuses_under_a_nondefault_thumbnail_namer(self):
        # Under a hashed namer, thumbnails no longer start with their source
        # name, so the command must refuse rather than risk deleting them.
        self._live_piece_with_thumbnail()
        orphan = _save(f'{uuid.uuid4()}-x.png')
        with self.assertRaises(CommandError):
            call_command('cleanup_orphan_images', delete=True, no_input=True, min_age_days=0, stdout=StringIO())
        self.assertTrue(default_storage.exists(orphan))  # refused before deleting anything
