"""Backfill the blur-up placeholder (`Piece.image_lqip`) for existing images.

The upload path sets `image_lqip` for new pieces, but images already in storage
(pre-feature, or copied during a backend migration) have an empty placeholder.
This sweeps Piece rows that have an image but no placeholder and generates one.

Kept OUT of the request path on purpose: building a placeholder decodes the
full-resolution original, and doing that on concurrent anonymous detail views
exhausted a small server's memory. This runs sequentially (one image at a time)
and `_lqip_from_bytes` draft-decodes JPEGs, so memory stays bounded. Run it once
post-deploy — after `backfill_strip_image_metadata`, so it reads the stripped
originals (though `lqip_data_uri` cleans metadata itself either way).

Safety, modelled on the other backfills:
* Dry run by default — listing only; ``--apply`` writes.
* Idempotent: rows that already have a placeholder are skipped.
* Best-effort per file: an unreadable image is logged and left blank, never
  aborting the sweep (the detail page falls back to a bare ``<img>``).
* Scoped to ``default_storage`` (Piece.image); the private document store is
  untouched.
"""

from django.core.management.base import BaseCommand

from art.images import lqip_data_uri
from art.models import Piece


class Command(BaseCommand):
    help = 'Generate the blur-up placeholder (image_lqip) for pieces missing one.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Write the placeholders. Without this flag the command only lists what it would do (dry run).',
        )

    def handle(self, *args, **opts):
        apply = opts['apply']
        filled = failed = 0

        missing = Piece.objects.exclude(image='').exclude(image__isnull=True).filter(image_lqip='')
        for piece in missing.iterator():
            uri = lqip_data_uri(piece.image)  # best-effort, draft-decoded; '' on failure
            if not uri:
                self.stderr.write(f'  ! could not build placeholder for {piece.image.name}')
                failed += 1
                continue
            self.stdout.write(f'  lqip: {piece.image.name}')
            if apply:
                Piece.objects.filter(pk=piece.pk).update(image_lqip=uri)
            filled += 1

        verb = 'filled' if apply else 'would fill'
        tail = '' if apply else '  (dry run; pass --apply to write)'
        self.stdout.write(self.style.SUCCESS(f'Done — {verb} {filled}; {failed} failed.{tail}'))
