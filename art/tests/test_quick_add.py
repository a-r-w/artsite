"""Tests for Quick add — bulk photo capture into draft pieces.

Two properties matter most here and are asserted from both ends:
  * a captured photo lands as a DRAFT (invisible publicly — see
    DraftVisibilityTests in test_public_views.py), and
  * it goes through the same upload validation as the regular form, so this
    isn't a looser path into storage (format allowlist, EXIF strip).
"""

import io
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from art.models import (
    PLACEHOLDER_LABEL,
    Artist,
    Location,
    Piece,
    SiteSettings,
    placeholder_artist,
    placeholder_location,
)

from .factories import make_artist, make_location, make_piece, make_staff, tiny_png
from .test_images import exif_jpeg


class QuickAddTestCase(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)
        self.url = reverse('art:curate:quick-add')

    def post_photos(self, *uploads):
        return self.client.post(self.url, {'photos': list(uploads)}, follow=True)

    @staticmethod
    def messages(resp):
        return [m.message for m in resp.context['messages']]


class QuickAddPageTests(QuickAddTestCase):
    def test_page_renders_with_both_capture_controls(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        # The batch picker and the straight-to-camera control are the whole UI;
        # `capture` is what makes an iPhone open the camera rather than a picker.
        self.assertContains(resp, 'name="photos"')
        self.assertContains(resp, 'multiple')
        self.assertContains(resp, 'capture="environment"')

    def test_page_links_to_the_queue_of_captures_needing_details(self):
        self.post_photos(tiny_png())
        resp = self.client.get(self.url)
        self.assertContains(resp, 'has_title=no')  # works in either mode
        self.assertContains(resp, 'capture waiting for details')

    def test_no_queue_section_when_there_is_nothing_waiting(self):
        self.assertNotContains(self.client.get(self.url), 'waiting for details')


class QuickAddCaptureTests(QuickAddTestCase):
    def test_photos_become_draft_pieces_with_placeholder_metadata(self):
        resp = self.post_photos(tiny_png('one.png'), tiny_png('two.png'))
        self.assertEqual(resp.status_code, 200)
        pieces = Piece.objects.all()
        self.assertEqual(pieces.count(), 2)
        for piece in pieces:
            self.assertTrue(piece.draft, 'a captured photo must start as a draft')
            self.assertEqual(piece.title, '')  # genuinely unset, not a fabricated title
            self.assertEqual(piece.artist.name, PLACEHOLDER_LABEL)
            self.assertEqual(piece.location.description, PLACEHOLDER_LABEL)
            self.assertTrue(piece.image, 'the photo itself is the point')

    def test_captured_pieces_get_distinct_provisional_slugs(self):
        self.post_photos(tiny_png('one.png'), tiny_png('two.png'), tiny_png('three.png'))
        slugs = sorted(Piece.objects.values_list('slug', flat=True))
        self.assertEqual(len(set(slugs)), 3, 'slugs must be unique')
        for slug in slugs:
            # Derived from the piece's own id, so captures never compete for a
            # shared "not-set-N" namespace — and the placeholder's NAME never
            # ends up in a URL that might later be frozen.
            self.assertTrue(slug.startswith('capture-'), slug)
            self.assertNotIn('not-set', slug)
        self.assertTrue(all(p.slug_is_provisional() for p in Piece.objects.all()))

    def test_a_completed_capture_never_hands_its_slug_to_another_piece(self):
        # The severe one: with drafts off these URLs are publicly served, so
        # reissuing a vacated slug would make a bookmark or an NFC tag silently
        # start showing a DIFFERENT artwork.
        self.post_photos(tiny_png('a.png'), tiny_png('b.png'))
        first = Piece.objects.order_by('created', 'id').first()
        vacated = first.slug
        first.artist = make_artist(name='Ada Lovelace')
        first.title = 'Analytical Engine'
        first.draft = False
        first.save()
        self.assertNotEqual(first.slug, vacated)

        self.post_photos(tiny_png('c.png'))
        self.assertFalse(
            Piece.objects.filter(slug=vacated).exists(),
            'a vacated capture URL must never be reused by a different piece',
        )

    def test_one_placeholder_row_is_shared_across_batches(self):
        self.post_photos(tiny_png('a.png'))
        self.post_photos(tiny_png('b.png'))
        self.assertEqual(Artist.objects.filter(name=PLACEHOLDER_LABEL).count(), 1)
        self.assertEqual(Location.objects.filter(description=PLACEHOLDER_LABEL).count(), 1)

    def test_a_curators_own_not_set_artist_is_reused_not_duplicated(self):
        # Artist.name isn't unique, so get_or_create would raise here if the
        # curator had already made their own "Not set" artist by hand.
        existing = make_artist(name=PLACEHOLDER_LABEL)
        self.post_photos(tiny_png())
        self.assertEqual(Artist.objects.filter(name=PLACEHOLDER_LABEL).count(), 1)
        self.assertEqual(Piece.objects.get().artist_id, existing.id)

    def test_reports_how_many_landed(self):
        resp = self.post_photos(tiny_png('a.png'), tiny_png('b.png'))
        self.assertIn('Added 2 drafts.', ' '.join(self.messages(resp)))

    def test_empty_submit_changes_nothing(self):
        resp = self.client.post(self.url, {}, follow=True)
        self.assertEqual(Piece.objects.count(), 0)
        self.assertIn('No photos were selected.', self.messages(resp))


class QuickAddValidationTests(QuickAddTestCase):
    def test_a_rejected_photo_does_not_cost_the_rest_of_the_batch(self):
        # One unusable file among twenty shouldn't throw away a whole photo walk.
        bad = SimpleUploadedFile('notes.txt', b'this is not an image', content_type='text/plain')
        resp = self.post_photos(tiny_png('good.png'), bad)
        self.assertEqual(Piece.objects.count(), 1)
        self.assertTrue(any('notes.txt' in m for m in self.messages(resp)))

    def test_unsupported_image_format_is_rejected(self):
        # Same allowlist as the regular form: a format the EXIF strip can't
        # re-encode must not reach storage.
        buf = io.BytesIO()
        Image.new('RGB', (8, 8)).save(buf, format='BMP')
        upload = SimpleUploadedFile('scan.bmp', buf.getvalue(), content_type='image/bmp')
        resp = self.post_photos(upload)
        self.assertEqual(Piece.objects.count(), 0)
        self.assertTrue(any('scan.bmp' in m for m in self.messages(resp)))

    def test_exif_is_stripped_from_a_captured_photo(self):
        # The images are public once published, and a phone photo carries GPS.
        # Piece.save() strips on upload; prove it holds on this path too.
        self.post_photos(exif_jpeg(name='beach.jpg'))
        piece = Piece.objects.get()
        with default_storage.open(piece.image.name, 'rb') as fh:
            stored = Image.open(io.BytesIO(fh.read()))
        self.assertEqual(list(stored.getexif().keys()), [])

    def test_stored_filename_does_not_keep_the_client_name(self):
        # A phone filename can encode date/place; the public URL must not.
        self.post_photos(tiny_png('IMG_2019_marthas_vineyard.png'))
        self.assertNotIn('marthas', Piece.objects.get().image.name)


class QuickAddPublishImmediatelyTests(QuickAddTestCase):
    """The SiteSettings opt-out: captures go straight onto the public site.

    The draft safety net is the default; a curator who'd rather see photos
    appear as they walk can turn it off in /curate/settings/.
    """

    def setUp(self):
        super().setUp()
        settings_row = SiteSettings.load()
        settings_row.quick_add_drafts = False
        settings_row.save()

    def test_captures_are_published_not_drafted(self):
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        self.assertFalse(piece.draft)

    def test_captured_piece_is_publicly_visible_straight_away(self):
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        anon = Client()
        self.assertEqual(anon.get(reverse('art:piece', kwargs={'slug': piece.slug})).status_code, 200)
        self.assertContains(anon.get(reverse('art:index')), 'Untitled')

    def test_the_message_says_they_are_live(self):
        resp = self.post_photos(tiny_png())
        self.assertIn('live on the public site', ' '.join(self.messages(resp)))

    def test_url_still_settles_when_the_details_are_filled_in(self):
        # Published-but-untitled is the one case where a piece is public with a
        # placeholder slug. It must not freeze there, or every photo walk leaves
        # permanent /not-set-N/ URLs behind.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        self.assertTrue(piece.slug_is_provisional())

        piece.artist = make_artist(name='Ada Lovelace')
        piece.title = 'Analytical Engine'
        piece.save()
        self.assertEqual(piece.slug, 'ada-lovelace-analytical-engine')

        piece.title = 'Renamed Later'
        piece.save()
        self.assertEqual(piece.slug, 'ada-lovelace-analytical-engine', 'a settled URL must not move')

    def test_nfc_tag_writing_is_withheld_until_the_url_settles(self):
        # A tag written against a provisional URL would break when the curator
        # finishes the piece, and re-writing wall tags is a chore.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        url = reverse('art:piece', kwargs={'slug': piece.slug})
        self.assertNotContains(self.client.get(url), 'smartnfc://')

        piece.title = 'Now Named'
        piece.artist = make_artist(name='Real Artist')
        piece.save()
        self.assertContains(self.client.get(reverse('art:piece', kwargs={'slug': piece.slug})), 'smartnfc://')

    def test_two_pass_completion_through_the_real_edit_form(self):
        # Same as the model-level test below, but driven through the curate form
        # the curator actually uses — the paths differ enough to be worth both.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        artist = make_artist(name='Berthe Morisot')
        location = make_location(description='Hallway')

        def edit(slug, **fields):
            payload = {
                'title': '',
                'artist': str(piece.artist_id),
                'location': str(piece.location_id),
                'medium': '',
                'medium_details': '',
                'acquired': '',
                'website': '',
                'notes': '',
                'notes_private': '',
                'date_acquired': '',
                'purchase_price': '',
            }
            payload.update(fields)
            self.client.post(reverse('art:curate:piece-edit', kwargs={'slug': slug}), payload)
            piece.refresh_from_db()

        edit(piece.slug, title='Evening Study')
        self.assertTrue(piece.slug_is_provisional(), piece.slug)

        edit(piece.slug, title='Evening Study', artist=str(artist.id), location=str(location.id))
        self.assertEqual(piece.slug, 'berthe-morisot-evening-study')

    def test_titling_first_and_assigning_the_artist_later_still_settles_cleanly(self):
        # A two-pass workflow (title the batch, then assign artists) must not
        # leave "not-set-" frozen into every URL.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()

        piece.title = 'Evening Study'
        piece.save()
        self.assertTrue(piece.slug_is_provisional(), 'still filed under the placeholder artist')

        piece.artist = make_artist(name='Berthe Morisot')
        piece.save()
        self.assertEqual(piece.slug, 'berthe-morisot-evening-study')
        self.assertFalse(piece.slug_is_provisional())

    def test_tagging_settles_the_url_even_while_untitled(self):
        # Backstop for a curator who ticks "tagged" by hand on an untitled piece:
        # something physical now points at the URL, so it stops moving.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        piece.tagged = True
        piece.save()
        tagged_slug = piece.slug

        piece.title = 'Titled After Tagging'
        piece.save()
        self.assertEqual(piece.slug, tagged_slug)


class QuickAddCompletionTests(QuickAddTestCase):
    """Finishing a draft: the curator fills in real metadata and publishes."""

    def test_publishing_rewrites_the_provisional_slug(self):
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        self.assertTrue(piece.slug_is_provisional())

        piece.artist = make_artist(name='Katsushika Hokusai')
        piece.title = 'Morning Tide'
        piece.draft = False
        piece.save()

        # The placeholder slug was never publicly reachable, so it isn't frozen
        # in — the piece gets the URL it would have had if entered by hand.
        self.assertEqual(piece.slug, 'katsushika-hokusai-morning-tide')

    def test_published_slug_is_then_stable(self):
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        piece.artist = make_artist(name='Ada Lovelace')
        piece.title = 'First'
        piece.draft = False
        piece.save()
        published_slug = piece.slug

        piece.title = 'Renamed Later'
        piece.save()
        self.assertEqual(piece.slug, published_slug, 'a published URL must not move')


class SlugStabilityTests(QuickAddTestCase):
    """Regression tests for the URL-stability rules.

    Every bug below shipped past a green suite because the original tests only
    walked the happy path (one piece in the database, completed in a single
    save). They are grouped here because they all turn on the same question —
    "can this piece's public URL still change?" — which is answered by the
    STORED `slug_settled` latch, not re-derived from current state.
    """

    def _published(self, title='Happy Trees', artist_name='Bob Ross'):
        piece = make_piece(title=title, artist=make_artist(name=artist_name))
        self.assertFalse(piece.slug_is_provisional())
        return piece

    def test_drafting_a_published_piece_does_not_reopen_its_url(self):
        # Hiding a live piece to fix a typo must not move the URL people already
        # have. Previously `provisional` was recomputed from current state, so
        # ticking Draft silently un-froze it.
        piece = self._published()
        original = piece.slug

        piece.draft = True
        piece.save()
        self.assertEqual(piece.slug, original, 'hiding a piece must not move its URL')

        piece.title = 'Happy Little Trees'
        piece.draft = False
        piece.save()
        self.assertEqual(piece.slug, original, 'republishing must not move its URL either')

    def test_clearing_the_title_of_a_published_piece_does_not_reopen_its_url(self):
        piece = self._published()
        original = piece.slug
        piece.title = ''
        piece.save()
        self.assertEqual(piece.slug, original)

    def test_tagging_never_moves_the_url_the_tag_encodes(self):
        # Needs SEVERAL captures: with only one in the database a regeneration
        # happens to land on the same string, which is why the original guard
        # was untested and broken.
        self.post_photos(tiny_png('a.png'), tiny_png('b.png'), tiny_png('c.png'))
        pieces = list(Piece.objects.order_by('created', 'id'))
        pieces[0].artist = make_artist(name='Someone Real')
        pieces[0].title = 'Finished'
        pieces[0].draft = False
        pieces[0].save()  # frees whatever slug it held

        target = pieces[2]
        tagged_url = target.slug
        target.tagged = True
        target.save()
        self.assertEqual(target.slug, tagged_url, 'a physical tag encodes this URL')
        self.assertFalse(target.slug_is_provisional(), 'tagging settles the URL')

        target.title = 'Named Afterwards'
        target.save()
        self.assertEqual(target.slug, tagged_url, 'and it stays put once settled')

    def test_completing_a_capture_mints_its_real_url_once_and_freezes_it(self):
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        piece.artist = make_artist(name='Berthe Morisot')
        piece.title = 'Evening Study'
        piece.draft = False
        piece.save()
        self.assertEqual(piece.slug, 'berthe-morisot-evening-study')

        piece.title = 'Renamed Later'
        piece.save()
        self.assertEqual(piece.slug, 'berthe-morisot-evening-study')


class PlaceholderIdentityTests(QuickAddTestCase):
    """The placeholder is a flagged ROW, not a name — matching on the string
    broke in both directions."""

    def test_renaming_the_placeholder_adopts_it_as_a_real_artist(self):
        # A whole walk by one artist: the curator renames "Not set" instead of
        # reassigning. The pieces must then settle under the NEW name rather
        # than freezing "not-set-" into their permanent URLs.
        self.post_photos(tiny_png())
        piece = Piece.objects.get()
        placeholder = piece.artist
        self.assertTrue(placeholder.is_placeholder)

        placeholder.name = 'Berthe Morisot'
        placeholder.save()
        placeholder.refresh_from_db()
        self.assertFalse(placeholder.is_placeholder, 'renaming adopts the row')

        piece.refresh_from_db()
        piece.title = 'Evening Study'
        piece.draft = False
        piece.save()
        self.assertEqual(piece.slug, 'berthe-morisot-evening-study')
        self.assertNotIn('not-set', piece.slug)

    def test_a_real_artist_named_not_set_is_not_mistaken_for_the_placeholder(self):
        # Someone who files unattributed works under a literal "Not set" artist
        # must still get normal, settling URLs.
        adopted = make_artist(name=PLACEHOLDER_LABEL)
        piece = make_piece(title='Anonymous Study', artist=adopted)
        self.assertFalse(piece.slug_is_provisional())
        original = piece.slug
        piece.title = 'Renamed'
        piece.save()
        self.assertEqual(piece.slug, original)

    def test_a_fully_rejected_batch_leaves_no_placeholder_rows_behind(self):
        # A folder of unsupported scans shouldn't permanently add "Not set" to
        # the artist and location dropdowns as a side effect of failing.
        bad = SimpleUploadedFile('notes.txt', b'not an image', content_type='text/plain')
        self.post_photos(bad)
        self.assertEqual(Piece.objects.count(), 0)
        self.assertFalse(Artist.objects.filter(is_placeholder=True).exists())
        self.assertFalse(Location.objects.filter(is_placeholder=True).exists())

    def test_only_one_placeholder_row_can_exist(self):
        # A partial unique index, so a concurrent double-submit can't silently
        # split a photo walk across two placeholders. (The name must match too:
        # a differently-named row has its flag cleared by _sync_placeholder_flag
        # before it ever reaches the constraint — asserted below.)
        placeholder_artist()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Artist.objects.create(name=PLACEHOLDER_LABEL, is_placeholder=True)

    def test_a_differently_named_row_cannot_claim_the_placeholder_flag(self):
        renamed = Artist.objects.create(name='Someone Real', is_placeholder=True)
        renamed.refresh_from_db()
        self.assertFalse(renamed.is_placeholder)


class BatchResilienceTests(QuickAddTestCase):
    def test_one_failed_photo_does_not_discard_the_rest_of_the_walk(self):
        # ATOMIC_REQUESTS wraps the whole POST, so without a per-photo savepoint
        # a storage error partway through would roll back everything that had
        # already succeeded.
        real_save = Piece.save
        state = {'n': 0}

        def flaky_save(self, *args, **kwargs):
            state['n'] += 1
            if state['n'] == 2:
                raise OSError('simulated storage failure')
            return real_save(self, *args, **kwargs)

        with patch.object(Piece, 'save', flaky_save):
            resp = self.post_photos(tiny_png('a.png'), tiny_png('b.png'), tiny_png('c.png'))

        self.assertEqual(Piece.objects.count(), 2, 'the good photos must survive')
        self.assertTrue(any('could not be saved' in m for m in self.messages(resp)))
        self.assertIn('Added 2 drafts.', ' '.join(self.messages(resp)))

    def test_slug_generation_does_not_degrade_with_the_size_of_the_backlog(self):
        # The provisional slug is derived from the piece's own id, so a batch
        # costs a constant number of queries per photo. The previous linear
        # collision probe cost one query per already-captured piece — 5,000+
        # queries for a 100-photo walk, and worse with a standing backlog.
        artist, location = placeholder_artist(), placeholder_location()

        def cost(n):
            Piece.objects.all().delete()
            with CaptureQueriesContext(connection) as ctx:
                for _ in range(n):
                    Piece.objects.create(title='', artist=artist, location=location, draft=True)
            return len(ctx.captured_queries) / n

        self.assertAlmostEqual(cost(5), cost(40), delta=1.0)


class SettledLatchDefaultsTests(TestCase):
    """What the 0029 data migration encodes, asserted against the live model so
    the two can't drift: which pre-existing pieces own their URL outright."""

    def test_an_ordinary_published_piece_owns_its_url_immediately(self):
        piece = make_piece(title='Sunflowers', artist=make_artist(name='Vincent'))
        self.assertFalse(piece.slug_is_provisional())

    def test_a_published_but_untitled_piece_does_not(self):
        piece = make_piece(title='', artist=make_artist(name='Vincent'))
        self.assertTrue(piece.slug_is_provisional())

    def test_a_published_piece_under_the_placeholder_artist_does_not(self):
        # The case that would otherwise freeze "not-set-…" into a live URL.
        piece = make_piece(title='Harbour Light', artist=placeholder_artist())
        self.assertTrue(piece.slug_is_provisional())
        self.assertNotIn('not-set', piece.slug)
