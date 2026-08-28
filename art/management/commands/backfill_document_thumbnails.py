"""Generate thumbnails for piece documents that lack one — e.g. documents
uploaded before thumbnails existed, or where upload-time generation failed.

Best-effort: a document whose file can't be rendered (unsupported/garbled) is
skipped and left thumbnail-less. Pass --all to regenerate every document's
thumbnail (e.g. after changing the thumbnail size).
"""

from django.core.management.base import BaseCommand

from art.models import PieceDocument


class Command(BaseCommand):
    help = 'Generate missing thumbnails for piece documents (--all to regenerate every one).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Regenerate even documents that already have a thumbnail.',
        )

    def handle(self, *args, **opts):
        made = skipped = 0
        for doc in PieceDocument.objects.all():
            if doc.thumbnail and not opts['all']:
                continue
            if doc.generate_thumbnail():
                made += 1
                self.stdout.write(f'  thumbnailed: {doc.original_name or doc.file.name}')
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(f'Done — {made} generated, {skipped} skipped.'))
