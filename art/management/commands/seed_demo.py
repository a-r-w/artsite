"""Populate the gallery with a small set of sample data for a first look.

So a fresh install isn't an empty page: a few artists, mediums, locations, and
pieces (with generated placeholder images). Idempotent — re-running is a no-op;
`--clear` removes exactly what it created and nothing a curator added.

    python manage.py seed_demo
    python manage.py seed_demo --clear
"""

import io
from datetime import date

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image, ImageDraw

from art.models import Artist, Location, Medium, Piece

# Stored in each demo piece's notes so --clear can find exactly what this command
# created (and never a piece a curator added by hand).
SENTINEL = '[seed_demo]'

ARTISTS = [
    ('Ada Lovelace', 'Works on paper exploring pattern and computation.'),
    ('Katsushika Hokusai', 'Edo-period painter and printmaker.'),
    ('Studio Mimosa', 'A contemporary collective working in mixed media.'),
]
MEDIUMS = ['Oil on canvas', 'Woodblock print', 'Photograph', 'Mixed media']
LOCATIONS = ['Living room', 'Studio', 'Hallway']

# title, artist, medium, location, (w, h, unit), (top rgb, bottom rgb), size
PIECES = [
    (
        'Morning Tide',
        'Katsushika Hokusai',
        'Woodblock print',
        'Living room',
        (15, 10, 'in'),
        ((90, 140, 190), (235, 225, 200)),
        (1000, 700),
    ),
    (
        'Analytical Engine No. 3',
        'Ada Lovelace',
        'Oil on canvas',
        'Studio',
        (24, 36, 'in'),
        ((40, 40, 70), (200, 120, 90)),
        (760, 1000),
    ),
    (
        'Hallway Light',
        'Studio Mimosa',
        'Photograph',
        'Hallway',
        (12, 18, 'in'),
        ((30, 30, 34), (120, 120, 135)),
        (760, 1000),
    ),
    (
        'Wave Fragment',
        'Katsushika Hokusai',
        'Woodblock print',
        'Studio',
        (10, 10, 'in'),
        ((20, 60, 90), (180, 210, 220)),
        (900, 900),
    ),
    (
        'Untitled (Mimosa I)',
        'Studio Mimosa',
        'Mixed media',
        'Living room',
        (30, 40, 'in'),
        ((200, 160, 60), (90, 40, 80)),
        (820, 1000),
    ),
    (
        'Notes in Blue',
        'Ada Lovelace',
        'Mixed media',
        'Hallway',
        (18, 24, 'in'),
        ((50, 80, 160), (225, 232, 245)),
        (820, 1000),
    ),
]


def _placeholder(title, top, bottom, size):
    """A vertical-gradient JPEG with the title as a small caption."""
    w, h = size
    img = Image.new('RGB', size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    draw.text((28, h - 34), title, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return ContentFile(buf.getvalue(), name='seed.jpg')


class Command(BaseCommand):
    help = 'Populate the gallery with a few sample pieces (demo data; --clear removes them).'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Remove the demo data instead of creating it.')

    @transaction.atomic
    def handle(self, *args, **options):
        if options['clear']:
            return self._clear()

        if Piece.objects.filter(notes__contains=SENTINEL).exists():
            self.stdout.write('Demo data already present — nothing to do (use --clear to remove it).')
            return

        artists = {name: Artist.objects.get_or_create(name=name, defaults={'notes': bio})[0] for name, bio in ARTISTS}
        mediums = {d: Medium.objects.get_or_create(description=d)[0] for d in MEDIUMS}
        locations = {d: Location.objects.get_or_create(description=d)[0] for d in LOCATIONS}

        for title, artist, medium, location, (width, height, unit), (top, bottom), size in PIECES:
            piece = Piece(
                title=title,
                artist=artists[artist],
                medium=mediums[medium],
                location=locations[location],
                width=width,
                height=height,
                dimension_unit=unit,
                acquired='Acquired for the sample collection',
                date_acquired=date(2024, 1, 1),
                notes=f'Sample piece. {SENTINEL}',
            )
            piece.image = _placeholder(title, top, bottom, size)
            piece.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'Seeded {len(PIECES)} pieces across {len(ARTISTS)} artists. '
                'Open / to browse, or /curate/ to edit. Run with --clear to remove.'
            )
        )

    def _clear(self):
        pieces = Piece.objects.filter(notes__contains=SENTINEL)
        removed_pieces = pieces.count()
        for piece in pieces:
            piece.delete()  # post_delete signal removes the image + thumbnails

        removed_taxonomy = 0
        for model, field, names in (
            (Artist, 'name', [name for name, _ in ARTISTS]),
            (Medium, 'description', MEDIUMS),
            (Location, 'description', LOCATIONS),
        ):
            for obj in model.objects.filter(**{f'{field}__in': names}):
                if not obj.piece_set.exists():  # don't remove one a curator reused
                    obj.delete()
                    removed_taxonomy += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Removed {removed_pieces} demo pieces and {removed_taxonomy} unused demo artists/mediums/locations.'
            )
        )
