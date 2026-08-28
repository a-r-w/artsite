"""Coverage for small helpers and project-level guards."""

import importlib
import uuid

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, TestCase

from art.models import Piece
from art.views_curate import _to_uuid
from artsite import settings as base_settings

from .factories import make_piece, tiny_png

# Helpers live in digit-prefixed migration modules; import them by module path
# (a normal `import` can't reference a name starting with a digit).
_dedupe = importlib.import_module('art.migrations.0013_alter_location_description_alter_medium_description')._dedupe
_backfill_dimensions = importlib.import_module(
    'art.migrations.0016_piece_image_height_piece_image_width'
).backfill_image_dimensions


class ToUuidTests(TestCase):
    def test_parses_valid_uuid(self):
        u = uuid.uuid4()
        self.assertEqual(_to_uuid(str(u)), u)

    def test_returns_none_for_garbage(self):
        self.assertIsNone(_to_uuid('not-a-uuid'))

    def test_returns_none_for_empty_and_none(self):
        self.assertIsNone(_to_uuid(''))
        self.assertIsNone(_to_uuid(None))


class DedupeHelperTests(TestCase):
    def test_unique_descriptions_unchanged(self):
        self.assertEqual(_dedupe(['Oil', 'Acrylic']), ['Oil', 'Acrylic'])

    def test_duplicates_get_trailing_integers(self):
        self.assertEqual(_dedupe(['Oil', 'Oil', 'Oil']), ['Oil', 'Oil 2', 'Oil 3'])

    def test_skips_an_existing_suffix_to_avoid_a_new_collision(self):
        # A real 'Oil 2' must survive; the renamed duplicate skips past it.
        self.assertEqual(_dedupe(['Oil', 'Oil', 'Oil 2']), ['Oil', 'Oil 3', 'Oil 2'])


class BackfillImageDimensionsTests(TestCase):
    """The 0016 data migration that fills dimensions for pre-existing images."""

    def _null_dims(self, piece):
        Piece.objects.filter(pk=piece.pk).update(image_width=None, image_height=None)

    def test_fills_dimensions_from_stored_file(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        self._null_dims(piece)  # simulate a row created before the columns existed
        _backfill_dimensions(apps, None)
        piece.refresh_from_db()
        self.assertEqual((piece.image_width, piece.image_height), (4, 4))

    def test_skips_a_piece_whose_file_is_missing(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        piece.image.storage.delete(piece.image.name)  # file vanished from storage
        self._null_dims(piece)
        _backfill_dimensions(apps, None)  # must not raise
        piece.refresh_from_db()
        self.assertIsNone(piece.image_width)

    def test_ignores_pieces_without_an_image(self):
        piece = make_piece()  # no image
        # Stamp a sentinel an imageless row should never carry, and prove the
        # backfill leaves it UNTOUCHED rather than processing it. The old
        # assertIsNone just re-checked the pre-existing null, so it passed even
        # if the backfill had written to (clobbered) the row.
        Piece.objects.filter(pk=piece.pk).update(image_width=999)
        _backfill_dimensions(apps, None)
        piece.refresh_from_db()
        self.assertEqual(piece.image_width, 999)


class StorageBackendSelectionTests(SimpleTestCase):
    """The STORAGE_BACKEND toggle (artsite.settings) selects the media-storage
    backend for both supported deployments — GCS (Fly.io) and local
    filesystem (self-hosted podman) — and fails closed on anything else."""

    def test_gcs_maps_to_google_cloud_storage(self):
        self.assertEqual(
            base_settings.storage_backend_path('gcs'),
            'storages.backends.gcloud.GoogleCloudStorage',
        )

    def test_local_maps_to_filesystem_storage(self):
        self.assertEqual(
            base_settings.storage_backend_path('local'),
            'django.core.files.storage.FileSystemStorage',
        )

    def test_unknown_backend_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            base_settings.storage_backend_path('s3')

    def test_private_gcs_prefix_is_a_sibling_not_a_child_of_the_public_location(self):
        # If the private prefix sat UNDER the public location (e.g. art/private),
        # cleanup_orphan_images' public-store walk would descend into it and
        # --delete the documents, and the GCS→local media rsync would copy them
        # into the public tree. Keeping it a sibling prevents both, structurally.
        public = base_settings._GS_LOCATION
        private = base_settings._private_storage_config('gcs')['OPTIONS']['location']
        self.assertFalse(private.startswith(public + '/'), f'{private!r} must not live under {public!r}')

    def test_private_gcs_store_is_owner_only_not_authenticated_readable(self):
        # Private documents must be owner-only at the object level, NOT inherit
        # the global GS_DEFAULT_ACL=authenticatedRead (which any Google account
        # could read). The gated view is the only intended reader.
        acl = base_settings._private_storage_config('gcs')['OPTIONS'].get('default_acl')
        self.assertEqual(acl, 'private')

    def test_source_and_thumbnail_storage_cannot_drift(self):
        # easy-thumbnails keeps its own storage setting; both the default store
        # and the thumbnail store derive from one _MEDIA_BACKEND, so they must
        # always resolve to the same backend (or sources and thumbnails split).
        self.assertEqual(
            base_settings.STORAGES['default']['BACKEND'],
            base_settings.THUMBNAIL_DEFAULT_STORAGE,
        )


class MigrationStateTests(TestCase):
    def test_models_have_matching_migrations(self):
        """Editing a model without running makemigrations should fail CI."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps))
        changes = autodetector.changes(graph=loader.graph)
        self.assertEqual(
            changes.get('art', []),
            [],
            'Model changes without a migration — run `manage.py makemigrations`.',
        )
