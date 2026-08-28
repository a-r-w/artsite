"""Verify that every image referenced by the database is present in the
configured storage backend.

This is the inverse of ``cleanup_orphan_images`` (which finds stored files that
no live row references): it finds live rows whose file is *missing* from
storage. Run it after copying media between backends — e.g. when migrating from
GCS to the local filesystem backend — to confirm the copy was complete before
cutting over. It exits with an error if anything is missing, so a migration
script can gate on it.

Only source images are checked. Thumbnails are regenerated on demand by
easy-thumbnails, so a missing thumbnail self-heals on the next request and is
not a reason to block a cutover.
"""

from django.apps import apps
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import models as dj_models


class Command(BaseCommand):
    help = 'Verify every image referenced by the database exists in storage (inverse of cleanup_orphan_images).'

    def handle(self, *args, **opts):
        storage = default_storage
        references = list(self._referenced_files())
        self.stdout.write(f'Source images referenced by the database: {len(references)}')

        missing = []
        for ref in references:
            try:
                present = storage.exists(ref['name'])
            except Exception as exc:
                raise CommandError(f'Could not query the storage backend: {exc}') from exc
            if not present:
                missing.append(ref)

        if not missing:
            self.stdout.write(self.style.SUCCESS('All referenced images are present in storage.'))
            return

        self.stdout.write(self.style.ERROR(f'\nMissing {len(missing)} referenced image(s):'))
        for ref in missing:
            self.stdout.write(f'  - {ref["name"]}  ({ref["model"]}.{ref["field"]} pk={ref["pk"]})')
        raise CommandError(
            f'{len(missing)} referenced image(s) are missing from storage — see the list above. '
            'Do not cut over until every file is present.'
        )

    @staticmethod
    def _referenced_files():
        """Yield {model, field, pk, name} for every non-empty FileField/ImageField
        value on every row in the art app — the same set ``cleanup_orphan_images``
        treats as in use, but with per-row context so a missing file can be traced
        back to the record that needs it."""
        for model in apps.get_app_config('art').get_models():
            # Only fields in the default (public) store; private-store fields
            # (e.g. a piece's documents) are verified by their own tooling, not
            # against default_storage, so they must be skipped here.
            fields = [
                f.name
                for f in model._meta.get_fields()
                if isinstance(f, dj_models.FileField) and f.storage is default_storage
            ]
            if not fields:
                continue
            label = model._meta.label  # e.g. 'art.Piece'
            for row in model.objects.values('pk', *fields):
                for field in fields:
                    name = row.get(field)
                    if name:
                        yield {'model': label, 'field': field, 'pk': row['pk'], 'name': name}
