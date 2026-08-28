"""Strip EXIF/XMP/IPTC metadata from public images already in storage.

The upload path strips new uploads (art.images.strip_image_metadata), but images
stored before that shipped — and any copied during a GCS<->local migration — keep
their camera's GPS / serial / timestamp metadata, which is served publicly. This
sweeps the existing collection: for every public image (Piece.image,
Artist.portrait — every FileField in the DEFAULT store) it re-strips the bytes
and, while it's there, renames the object to the stem-less ``<uuid><ext>`` scheme
(the old ``<uuid>-<client-name>`` form leaked the original filename in the public
URL too), regenerating thumbnails.

Safety, modelled on cleanup_orphan_images / verify_media:
* Dry run by default — listing only; ``--apply`` performs the rewrite.
* Scoped strictly to ``default_storage``; the private document store is untouched.
* Idempotent: an image already metadata-clean AND on the stem-less name is
  skipped, so re-runs (and an already-clean library) do nothing.
* Best-effort per file: an unreadable / un-re-encodable file is logged and left
  as-is, never aborting the sweep (and never overwritten with a failed encode).
* Runs against whatever STORAGE_BACKEND / ENVIRONMENT is configured.
"""

import io
import os

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import models as dj_models
from easy_thumbnails.files import get_thumbnailer
from PIL import Image

from art.images import _lqip_from_bytes, image_has_metadata, strip_metadata_bytes


def _public_image_fields():
    """(model, field_name) for every FileField/ImageField on the art app stored in
    default_storage — Piece.image, Artist.portrait, and any future public image
    field. Private-store fields (PieceDocument) are excluded by construction."""
    for model in apps.get_app_config('art').get_models():
        for field in model._meta.get_fields():
            if isinstance(field, dj_models.FileField) and field.storage is default_storage:
                yield model, field.name


class Command(BaseCommand):
    help = 'Strip EXIF/metadata (and drop the leaked client filename) from images already in storage.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Rewrite the files. Without this flag the command only lists what it would do (dry run).',
        )

    def handle(self, *args, **opts):
        apply = opts['apply']
        cleaned = renamed = skipped = failed = 0

        for model, field_name in _public_image_fields():
            for instance in model.objects.all().iterator():
                fieldfile = getattr(instance, field_name)
                if not fieldfile or not fieldfile.name:
                    continue
                old_name = fieldfile.name
                ext = os.path.splitext(old_name)[1].lower()
                new_name = f'{instance.pk}{ext}'
                needs_rename = os.path.basename(old_name) != new_name

                try:
                    fieldfile.open('rb')
                    try:
                        data = fieldfile.read()
                    finally:
                        fieldfile.close()
                except Exception as exc:
                    self.stderr.write(f'  ! cannot read {old_name}: {exc}')
                    failed += 1
                    continue

                dirty = image_has_metadata(data)
                if not dirty and not needs_rename:
                    skipped += 1
                    continue

                actions = (['strip'] if dirty else []) + (['rename'] if needs_rename else [])
                arrow = f' -> {new_name}' if needs_rename else ''
                self.stdout.write(f'  {",".join(actions)}: {old_name}{arrow}')
                if not apply:
                    if dirty:
                        cleaned += 1
                    if needs_rename:
                        renamed += 1
                    continue

                out = data
                if dirty:
                    try:
                        stripped = strip_metadata_bytes(data)
                    except Exception as exc:
                        # Never overwrite an irreplaceable original with a failed
                        # re-encode — log and leave it (fail closed on the rewrite).
                        self.stderr.write(f'  ! cannot re-encode {old_name}: {exc} (left as-is)')
                        failed += 1
                        continue
                    if stripped is not None:
                        out = stripped

                # Old thumbnails are keyed off the old source name — drop them so
                # they regenerate clean from the rewritten source.
                try:
                    get_thumbnailer(fieldfile).delete_thumbnails()
                except Exception:
                    pass

                target = new_name if needs_rename else old_name
                if default_storage.exists(target):
                    default_storage.delete(target)
                saved = default_storage.save(target, ContentFile(out))
                if needs_rename and saved != old_name:
                    default_storage.delete(old_name)

                update = {field_name: saved}
                if field_name == 'image' and hasattr(instance, 'image_width'):
                    with Image.open(io.BytesIO(out)) as img:
                        update['image_width'], update['image_height'] = img.size
                    try:  # blur-up placeholder (best-effort, cosmetic)
                        update['image_lqip'] = _lqip_from_bytes(out)
                    except Exception:
                        pass
                model.objects.filter(pk=instance.pk).update(**update)

                if dirty:
                    cleaned += 1
                if needs_rename:
                    renamed += 1

        verb = 'rewrote' if apply else 'would rewrite'
        tail = '' if apply else '  (dry run; pass --apply to write)'
        self.stdout.write(
            self.style.SUCCESS(
                f'Done — {verb} {cleaned} stripped, {renamed} renamed; {skipped} already clean, {failed} failed.{tail}'
            )
        )
