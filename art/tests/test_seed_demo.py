"""The seed_demo management command — sample data for a first look."""

from django.core.management import call_command
from django.test import TestCase

from art.models import Artist, Location, Medium, Piece


class SeedDemoTests(TestCase):
    def test_seeds_a_browsable_gallery_with_images(self):
        call_command('seed_demo')
        self.assertEqual(Piece.objects.count(), 6)
        self.assertEqual(Artist.objects.count(), 3)
        self.assertEqual(Medium.objects.count(), 4)
        self.assertEqual(Location.objects.count(), 3)
        # Every piece has an image that went through the save pipeline (dimensions
        # captured), so the gallery and detail pages render real thumbnails.
        self.assertTrue(all(p.image for p in Piece.objects.all()))
        self.assertFalse(Piece.objects.filter(image_width=None).exists())

    def test_is_idempotent(self):
        call_command('seed_demo')
        call_command('seed_demo')  # second run is a no-op, not a duplicate
        self.assertEqual(Piece.objects.count(), 6)
        self.assertEqual(Medium.objects.count(), 4)

    def test_clear_removes_only_the_demo_data(self):
        # A real piece the curator added (reusing a demo medium/location) must survive.
        call_command('seed_demo')
        keeper_medium = Medium.objects.get(description='Oil on canvas')
        keeper_location = Location.objects.get(description='Living room')
        keeper_artist = Artist.objects.create(name='A Real Artist')
        Piece.objects.create(title='A Real Piece', artist=keeper_artist, medium=keeper_medium, location=keeper_location)

        call_command('seed_demo', '--clear')

        self.assertEqual(Piece.objects.filter(notes__contains='[seed_demo]').count(), 0)
        self.assertEqual(Piece.objects.count(), 1)  # the curator's piece remains
        # Taxonomy still referenced by the keeper survives; the rest is gone.
        self.assertTrue(Medium.objects.filter(pk=keeper_medium.pk).exists())
        self.assertTrue(Location.objects.filter(pk=keeper_location.pk).exists())
        self.assertFalse(Medium.objects.filter(description='Woodblock print').exists())
        self.assertFalse(Artist.objects.filter(name='Ada Lovelace').exists())
