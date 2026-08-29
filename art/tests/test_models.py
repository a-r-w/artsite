"""Tests for art/models.py — slug generation, string reprs, helpers."""

from unittest import mock

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from art.models import Artist, Location, Medium, Piece, PieceDocument, SiteSettings, id_prefixed_filename

from .factories import make_artist, make_document, make_location, make_medium, make_piece, tiny_pdf, tiny_png


class UniqueDescriptionTests(TestCase):
    def test_duplicate_medium_description_rejected(self):
        make_medium(description='Etching')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Medium.objects.create(description='Etching')

    def test_duplicate_location_description_rejected(self):
        make_location(description='Attic')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Location.objects.create(description='Attic')


class StrAndOrderingTests(TestCase):
    def test_str_methods(self):
        self.assertEqual(str(make_artist(name='Yayoi Kusama')), 'Yayoi Kusama')
        self.assertEqual(str(make_medium('Acrylic')), 'Acrylic')
        self.assertEqual(str(make_location('Hallway')), 'Hallway')
        self.assertEqual(str(make_piece(title='Infinity Net')), 'Infinity Net')

    def test_default_ordering(self):
        make_artist(name='Zoe')
        make_artist(name='Anna')
        self.assertEqual([a.name for a in Artist.objects.all()], ['Anna', 'Zoe'])


class FieldDefaultTests(TestCase):
    def test_tagged_defaults_to_false(self):
        """A piece can be created without specifying `tagged` (no NOT NULL error)."""
        piece = Piece.objects.create(title='Fresh', artist=make_artist(), location=make_location())
        self.assertFalse(piece.tagged)

    def test_created_and_modified_are_autoset(self):
        piece = make_piece()
        self.assertIsNotNone(piece.created)
        self.assertIsNotNone(piece.modified)


class IdPrefixedFilenameTests(TestCase):
    def test_names_file_by_uuid_dropping_the_client_stem(self):
        # The client filename stem is dropped (privacy: it's public in the image
        # URL); only the UUID + lowercased extension are kept.
        artist = make_artist()
        self.assertEqual(id_prefixed_filename(artist, 'Holiday_2019.JPG'), f'{artist.id}.jpg')
        self.assertEqual(id_prefixed_filename(artist, 'photo.jpeg'), f'{artist.id}.jpeg')


class SlugGenerationTests(TestCase):
    def test_slug_built_from_artist_and_title(self):
        piece = make_piece(title='The Two Fridas', artist=make_artist(name='Frida Kahlo'))
        self.assertEqual(piece.slug, 'frida-kahlo-the-two-fridas')

    def test_collisions_get_numeric_suffixes(self):
        artist = make_artist(name='Frida Kahlo')
        loc = make_location()
        slugs = [make_piece(title='Roots', artist=artist, location=loc).slug for _ in range(3)]
        self.assertEqual(
            slugs,
            ['frida-kahlo-roots', 'frida-kahlo-roots-1', 'frida-kahlo-roots-2'],
        )

    def test_slug_not_regenerated_on_update(self):
        """Renaming a piece keeps its original slug (stable public URLs)."""
        piece = make_piece(title='Original')
        original_slug = piece.slug
        piece.title = 'Renamed Entirely'
        piece.save()
        piece.refresh_from_db()
        self.assertEqual(piece.slug, original_slug)

    def test_explicit_slug_is_respected(self):
        piece = make_piece(title='Whatever')
        piece2 = Piece(
            title='Other',
            artist=piece.artist,
            location=piece.location,
            tagged=False,
            slug='hand-picked-slug',
        )
        piece2.save()
        self.assertEqual(piece2.slug, 'hand-picked-slug')

    def test_non_slug_integrity_error_is_not_retried_or_masked(self):
        """A database error from the real save must propagate immediately —
        not be mistaken for a slug collision and retried."""
        piece = Piece(title='Doomed', artist=make_artist(), location=make_location())
        with mock.patch('django.db.models.Model.save', side_effect=IntegrityError('boom')) as mocked_save:
            with self.assertRaises(IntegrityError):
                piece.save()
        self.assertEqual(mocked_save.call_count, 1)  # saved once, not retried 100×


class ForeignKeyDeleteBehaviourTests(TestCase):
    def test_deleting_artist_in_use_is_protected(self):
        artist = make_artist()
        make_piece(artist=artist)
        with self.assertRaises(ProtectedError):
            artist.delete()
        self.assertEqual(Piece.objects.count(), 1)
        self.assertTrue(Artist.objects.filter(pk=artist.pk).exists())

    def test_deleting_location_in_use_is_protected(self):
        loc = make_location()
        make_piece(location=loc)
        with self.assertRaises(ProtectedError):
            loc.delete()
        self.assertEqual(Piece.objects.count(), 1)
        self.assertTrue(Location.objects.filter(pk=loc.pk).exists())

    def test_deleting_medium_in_use_is_protected(self):
        medium = make_medium()
        make_piece(medium=medium)
        with self.assertRaises(ProtectedError):
            medium.delete()
        self.assertEqual(Piece.objects.count(), 1)
        self.assertTrue(Medium.objects.filter(pk=medium.pk).exists())

    def test_unreferenced_lookups_can_be_deleted(self):
        make_artist().delete()
        make_location().delete()
        make_medium().delete()
        self.assertEqual((Artist.objects.count(), Location.objects.count(), Medium.objects.count()), (0, 0, 0))


class ImageCleanupTests(TestCase):
    def test_deleting_piece_removes_its_image_file(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        name, storage = piece.image.name, piece.image.storage
        self.assertTrue(storage.exists(name))
        piece.delete()
        self.assertFalse(storage.exists(name))

    def test_deleting_piece_cleans_up_its_thumbnails(self):
        # Cleanup must remove generated thumbnails too, not just the source
        # image — otherwise they orphan in the bucket. delete_thumbnails() is
        # the only easy-thumbnails call that deletes thumbnail files, so assert
        # the post_delete signal invokes it. (The source-file removal itself is
        # covered by test_deleting_piece_removes_its_image_file.)
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        with mock.patch('art.models.get_thumbnailer') as mock_get_thumbnailer:
            piece.delete()
        mock_get_thumbnailer.return_value.delete_thumbnails.assert_called_once_with()

    def test_delete_tolerates_a_storage_failure(self):
        # A transient storage error during cleanup must not break the delete —
        # the row is already gone; it's logged, and cleanup_orphan_images is the
        # safety net for any file left behind.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        with mock.patch('art.models.get_thumbnailer', side_effect=OSError('storage down')):
            with self.assertLogs('art.models', level='WARNING'):
                piece.delete()  # must not raise
        self.assertFalse(Piece.objects.filter(pk=piece.pk).exists())

    def test_deleting_piece_without_image_is_safe(self):
        make_piece().delete()  # must not raise on an empty image field

    def test_replacing_piece_image_deletes_the_old_file(self):
        piece = make_piece()
        piece.image = tiny_png('first.png')
        piece.save()
        old_name, storage = piece.image.name, piece.image.storage
        self.assertTrue(storage.exists(old_name))
        piece.image = tiny_png('second.png')  # replace
        piece.save()
        self.assertNotEqual(piece.image.name, old_name)
        self.assertFalse(storage.exists(old_name))  # old source cleaned up
        self.assertTrue(storage.exists(piece.image.name))  # new one present

    def test_resaving_piece_without_a_new_image_keeps_the_file(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        name, storage = piece.image.name, piece.image.storage
        piece.title = 'Renamed'
        piece.save()  # image field unchanged
        self.assertTrue(storage.exists(name))

    def test_replacing_artist_portrait_deletes_the_old_file(self):
        artist = make_artist()
        artist.portrait = tiny_png('p1.png')
        artist.save()
        old_name, storage = artist.portrait.name, artist.portrait.storage
        artist.portrait = tiny_png('p2.png')  # replace
        artist.save()
        self.assertNotEqual(artist.portrait.name, old_name)
        self.assertFalse(storage.exists(old_name))

    def test_deleting_artist_removes_portrait(self):
        artist = make_artist()
        artist.portrait = tiny_png()
        artist.save()
        name, storage = artist.portrait.name, artist.portrait.storage
        self.assertTrue(storage.exists(name))
        artist.delete()
        self.assertFalse(storage.exists(name))


class PieceDocumentTests(TestCase):
    def test_document_file_lives_in_the_private_store_not_the_public_one(self):
        doc = make_document(make_piece())
        self.assertTrue(doc.file.storage.exists(doc.file.name))  # in the private store
        self.assertFalse(default_storage.exists(doc.file.name))  # NOT in the public store

    def test_document_file_has_no_public_url(self):
        # The private store refuses to mint a URL, so an accidental {{ doc.file.url }}
        # fails loudly rather than leaking the file (a signed, shareable URL on GCS).
        doc = make_document(make_piece())
        self.assertRaises(NotImplementedError, lambda: doc.file.url)

    def test_document_thumbnail_has_no_public_url(self):
        # A thumbnail of a receipt is as sensitive as the receipt — same private
        # store, no public URL, and never in the default (public) store.
        doc = make_document(make_piece(), name='photo.png', content=tiny_png().read())
        doc.generate_thumbnail()
        self.assertRaises(NotImplementedError, lambda: doc.thumbnail.url)
        self.assertFalse(default_storage.exists(doc.thumbnail.name))

    def test_deleting_a_document_removes_its_file(self):
        doc = make_document(make_piece())
        name, storage = doc.file.name, doc.file.storage
        self.assertTrue(storage.exists(name))
        doc.delete()
        self.assertFalse(storage.exists(name))

    def test_deleting_a_piece_cascades_and_removes_document_files(self):
        piece = make_piece()
        docs = [make_document(piece, name='a.pdf'), make_document(piece, name='b.pdf')]
        names, storage = [d.file.name for d in docs], docs[0].file.storage
        piece.delete()
        self.assertEqual(PieceDocument.objects.count(), 0)
        for name in names:
            self.assertFalse(storage.exists(name))

    def test_document_delete_tolerates_a_storage_failure(self):
        doc = make_document(make_piece())
        with mock.patch.object(type(doc.file.storage), 'delete', side_effect=OSError('storage down')):
            with self.assertLogs('art.models', level='WARNING'):
                doc.delete()  # must not raise
        self.assertFalse(PieceDocument.objects.filter(pk=doc.pk).exists())

    def test_generate_thumbnail_for_an_image(self):
        doc = make_document(make_piece(), name='photo.png', content=tiny_png().read())
        self.assertTrue(doc.generate_thumbnail())
        self.assertTrue(doc.thumbnail)
        self.assertTrue(doc.thumbnail.storage.exists(doc.thumbnail.name))

    def test_generate_thumbnail_for_a_pdf(self):
        doc = make_document(make_piece(), name='scan.pdf', content=tiny_pdf().read(), content_type='application/pdf')
        self.assertTrue(doc.generate_thumbnail())
        self.assertTrue(doc.thumbnail.storage.exists(doc.thumbnail.name))

    def test_generate_thumbnail_is_best_effort_for_unrenderable_content(self):
        doc = make_document(make_piece(), name='broken.pdf', content=b'not a pdf', content_type='application/pdf')
        self.assertFalse(doc.generate_thumbnail())
        self.assertFalse(doc.thumbnail)

    def test_deleting_a_document_removes_its_thumbnail_too(self):
        doc = make_document(make_piece(), name='photo.png', content=tiny_png().read())
        doc.generate_thumbnail()
        thumb_name, storage = doc.thumbnail.name, doc.thumbnail.storage
        self.assertTrue(storage.exists(thumb_name))
        doc.delete()
        self.assertFalse(storage.exists(thumb_name))


class ImageDimensionTests(TestCase):
    def test_dimensions_populated_from_upload(self):
        piece = make_piece()
        piece.image = tiny_png()  # a 4x4 PNG
        piece.save()
        piece.refresh_from_db()
        self.assertEqual(piece.image_width, 4)
        self.assertEqual(piece.image_height, 4)

    def test_dimensions_stay_null_without_an_image(self):
        piece = make_piece()  # no image
        self.assertIsNone(piece.image_width)
        self.assertIsNone(piece.image_height)

    def test_dimensions_preserved_when_resaving_without_image_change(self):
        piece = make_piece()
        piece.image = tiny_png()  # a 4x4 PNG
        piece.save()
        # Stamp distinctive dimensions, then re-save after editing another
        # field. A correct save leaves the committed image UNREAD, so the
        # stamped values persist; a save that re-read the stored 4x4 file would
        # reset them to (4, 4). The sentinel is what makes "didn't re-read"
        # observable — equal-to-(4, 4) couldn't distinguish the two.
        Piece.objects.filter(pk=piece.pk).update(image_width=111, image_height=222)
        reloaded = Piece.objects.get(pk=piece.pk)
        reloaded.title = 'Renamed'
        reloaded.save()
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.title, 'Renamed')  # the edit persisted
        self.assertEqual((reloaded.image_width, reloaded.image_height), (111, 222))

    def test_reading_dimensions_does_not_truncate_the_stored_file(self):
        # Reading width/height consumes the upload's file pointer; the saved
        # file must still be the complete, valid image afterwards.
        upload = tiny_png()
        original_bytes = upload.read()
        upload.seek(0)
        piece = make_piece()
        piece.image = upload
        piece.save()
        with piece.image.storage.open(piece.image.name, 'rb') as fh:
            stored_bytes = fh.read()
        self.assertEqual(stored_bytes, original_bytes)
        self.assertGreater(len(stored_bytes), 0)
        self.assertEqual((piece.image_width, piece.image_height), (4, 4))

    def test_ensure_image_dimensions_backfills_a_stored_image(self):
        # A legacy row whose size was never captured: the read-time backfill
        # populates it from the stored file.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        Piece.objects.filter(pk=piece.pk).update(image_width=None, image_height=None)
        piece = Piece.objects.get(pk=piece.pk)
        piece.ensure_image_dimensions()
        piece.refresh_from_db()
        self.assertEqual((piece.image_width, piece.image_height), (4, 4))

    def test_ensure_image_dimensions_is_a_noop_without_an_image(self):
        piece = make_piece()  # no image
        piece.ensure_image_dimensions()  # must not raise or fabricate a size
        piece.refresh_from_db()
        self.assertIsNone(piece.image_width)
        self.assertIsNone(piece.image_height)


class DimensionsDisplayTests(TestCase):
    def test_both_dimensions_with_trailing_zeros_stripped(self):
        piece = make_piece(width='24.50', height='36.00', dimension_unit='cm')
        self.assertEqual(piece.dimensions, '24.5 × 36 cm')

    def test_single_dimension(self):
        piece = make_piece(width='11.75', dimension_unit='in')
        self.assertEqual(piece.dimensions, '11.75 in')

    def test_dimensions_without_a_unit(self):
        piece = make_piece(width='10', height='20')  # dimension_unit defaults to ''
        self.assertEqual(piece.dimensions, '10 × 20')

    def test_empty_when_no_dimensions(self):
        self.assertEqual(make_piece().dimensions, '')


class SiteSettingsTests(TestCase):
    def test_load_returns_transient_defaults_when_unset(self):
        s = SiteSettings.load()
        self.assertIsNone(s.pk)  # not written until saved
        self.assertEqual(s.site_name, 'The Collection')
        self.assertIn('private collection', s.footer_text)
        self.assertEqual(str(s), 'Site settings')

    def test_save_enforces_a_single_row(self):
        first = SiteSettings.load()
        first.site_name = 'Gallery One'
        first.save()
        self.assertEqual(first.pk, 1)
        # A FRESH instance saved while row 1 exists must collapse onto pk=1
        # (the `self.pk = 1` override) — an UPDATE of row 1, never a 2nd INSERT.
        SiteSettings(site_name='Gallery Two').save()
        self.assertEqual(SiteSettings.objects.count(), 1)
        row = SiteSettings.objects.get()
        self.assertEqual(row.pk, 1)
        self.assertEqual(row.site_name, 'Gallery Two')  # the save took effect

    def test_public_sort_defaults_to_newest_acquired(self):
        self.assertEqual(SiteSettings.load().public_sort, SiteSettings.PublicSort.ACQUIRED_DESC)

    def test_public_ordering_maps_title_and_artist_choices(self):
        s = SiteSettings.load()
        # Each ordering ends in 'id' so it is total: untitled captures sharing
        # the placeholder artist tie on every other key.
        s.public_sort = SiteSettings.PublicSort.TITLE
        self.assertEqual(s.public_ordering(), ('title', 'id'))
        s.public_sort = SiteSettings.PublicSort.ARTIST
        self.assertEqual(s.public_ordering(), ('artist__name', 'title', 'id'))

    def test_public_ordering_falls_back_on_unknown_value(self):
        s = SiteSettings.load()
        default = s.public_ordering()  # load() defaults to ACQUIRED_DESC
        s.public_sort = 'bogus'
        self.assertEqual(s.public_ordering(), default)
