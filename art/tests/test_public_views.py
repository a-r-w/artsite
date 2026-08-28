"""Tests for the public site (art/views.py)."""

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from art.models import Piece, SiteSettings

from .factories import make_artist, make_location, make_piece, make_staff, make_user, tiny_png


class PublicListViewTests(TestCase):
    def test_index_ok(self):
        make_piece()
        resp = self.client.get(reverse('art:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('piece_list', resp.context)
        self.assertNotContains(resp, 'A personal gallery')  # tagline removed

    def test_footer_has_curate_link_for_anonymous(self):
        resp = self.client.get(reverse('art:index'))  # anonymous client
        self.assertContains(resp, f'href="{reverse("art:curate:home")}"')
        self.assertContains(resp, '>Curate</a>')

    def test_artists_ordered_by_artist_then_title(self):
        loc = make_location()
        zoe = make_artist(name='Zoe')
        anna = make_artist(name='Anna')
        # Title order is made to DISAGREE with artist order, so a title-only
        # sort would fail here: Anna's piece is 'Z', Zoe's is 'A'.
        make_piece(title='Z', artist=anna, location=loc)
        make_piece(title='A', artist=zoe, location=loc)
        resp = self.client.get(reverse('art:artists'))
        self.assertEqual(resp.status_code, 200)
        titles = [p.title for p in resp.context['piece_list']]
        self.assertEqual(titles, ['Z', 'A'])  # Anna (Z) before Zoe (A): grouped by artist, not title

    def test_index_ordered_by_acquired_date_desc(self):
        loc, artist = make_location(), make_artist()
        make_piece(title='Old', artist=artist, location=loc, date_acquired='2010-01-01')
        make_piece(title='New', artist=artist, location=loc, date_acquired='2024-01-01')
        make_piece(title='Undated', artist=artist, location=loc)
        titles = [p.title for p in self.client.get(reverse('art:index')).context['piece_list']]
        self.assertEqual(titles[0], 'New')  # most recently acquired first
        self.assertEqual(titles[-1], 'Undated')  # null dates last

    def test_index_sort_oldest_acquired_first(self):
        loc, artist = make_location(), make_artist()
        make_piece(title='Old', artist=artist, location=loc, date_acquired='2010-01-01')
        make_piece(title='New', artist=artist, location=loc, date_acquired='2024-01-01')
        make_piece(title='Undated', artist=artist, location=loc)
        SiteSettings.objects.create(public_sort=SiteSettings.PublicSort.ACQUIRED_ASC)
        titles = [p.title for p in self.client.get(reverse('art:index')).context['piece_list']]
        self.assertEqual(titles[0], 'Old')  # oldest acquired first
        self.assertEqual(titles[-1], 'Undated')  # null dates still trail in both directions

    def test_index_sort_by_title(self):
        loc, artist = make_location(), make_artist()
        # Acquisition order is made to DISAGREE so a leftover date sort would fail.
        make_piece(title='Beta', artist=artist, location=loc, date_acquired='2024-01-01')
        make_piece(title='Alpha', artist=artist, location=loc, date_acquired='2010-01-01')
        SiteSettings.objects.create(public_sort=SiteSettings.PublicSort.TITLE)
        titles = [p.title for p in self.client.get(reverse('art:index')).context['piece_list']]
        self.assertEqual(titles, ['Alpha', 'Beta'])

    def test_index_sort_by_artist_then_title(self):
        loc = make_location()
        zoe, anna = make_artist(name='Zoe'), make_artist(name='Anna')
        make_piece(title='Z', artist=anna, location=loc)  # Anna → 'Z'
        make_piece(title='A', artist=zoe, location=loc)  # Zoe → 'A'
        SiteSettings.objects.create(public_sort=SiteSettings.PublicSort.ARTIST)
        titles = [p.title for p in self.client.get(reverse('art:index')).context['piece_list']]
        self.assertEqual(titles, ['Z', 'A'])  # grouped by artist (Anna before Zoe), not title

    def test_staff_nav_shows_untagged_without_admin_link(self):
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:index'))
        self.assertContains(resp, f'href="{reverse("art:untagged")}"')  # Untagged present
        self.assertNotContains(resp, reverse('admin:index'))  # Admin link gone
        self.assertNotContains(resp, 'staff-only')  # Untagged styled like the rest

    def test_location_view_ok(self):
        make_piece()
        resp = self.client.get(reverse('art:location'))
        self.assertEqual(resp.status_code, 200)

    def test_robots_txt(self):
        resp = self.client.get(reverse('art:robots.txt'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/plain')
        self.assertContains(resp, 'Disallow: /')  # a private collection stays out of indexes


class UntaggedViewTests(TestCase):
    def test_staff_sees_only_untagged(self):
        make_piece(title='Done', tagged=True)
        make_piece(title='Todo', tagged=False)
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:untagged'))
        self.assertEqual(resp.status_code, 200)
        titles = {p.title for p in resp.context['piece_list']}
        self.assertEqual(titles, {'Todo'})

    def test_anonymous_redirected(self):
        resp = self.client.get(reverse('art:untagged'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_non_staff_forbidden(self):
        # Authenticated-but-unauthorised users get 403, not a login redirect.
        self.client.force_login(make_user())
        resp = self.client.get(reverse('art:untagged'))
        self.assertEqual(resp.status_code, 403)


class DetailViewTests(TestCase):
    def test_detail_ok(self):
        piece = make_piece(title='Sunflowers')
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Sunflowers')

    def test_staff_sees_edit_link_to_the_curate_page(self):
        piece = make_piece()
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        edit_url = reverse('art:curate:piece-edit', kwargs={'slug': piece.slug})
        self.assertContains(resp, f'href="{edit_url}"')
        self.assertContains(resp, '>Edit</a>')

    def test_edit_link_shows_even_when_tagged_but_nfc_does_not(self):
        # The NFC button is untagged-only; the Edit button is always there for staff.
        piece = make_piece(tagged=True)
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'smartnfc://')  # no Write NFC Tag button on a tagged piece

    def test_nfc_write_url_uses_the_request_host_not_a_hardcoded_domain(self):
        # The tag must point at THIS deployment's host, so a self-hoster's tags
        # open their own site — not whatever domain happened to be baked in.
        piece = make_piece()  # untagged -> NFC button present
        self.client.force_login(make_staff())
        path = reverse('art:piece', kwargs={'slug': piece.slug})
        resp = self.client.get(path)
        self.assertContains(resp, f'payload=http://testserver{path}?from=tag')

    def test_anonymous_sees_no_edit_link_or_staff_block(self):
        piece = make_piece()
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'staff-only')

    def test_non_staff_user_sees_no_edit_link(self):
        piece = make_piece()
        self.client.force_login(make_user())
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))

    def test_detail_shows_dimensions(self):
        piece = make_piece(width='24.50', height='36', dimension_unit='cm')
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'Dimensions')
        self.assertContains(resp, '24.5 × 36 cm')

    def test_detail_omits_dimensions_when_unset(self):
        piece = make_piece()  # no width/height
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'Dimensions')

    def test_detail_image_carries_dimensions_to_prevent_layout_shift(self):
        piece = make_piece(title='Sized')
        piece.image = tiny_png()  # 4x4
        piece.save()
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'width="4"')
        self.assertContains(resp, 'height="4"')
        self.assertContains(resp, 'aspect-ratio: 4 / 4')  # reserves the box at first paint

    def test_view_backfills_missing_image_dimensions(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        # Simulate a piece whose dimensions were never captured (e.g. uploaded
        # before the columns existed, or a backfill that ran without storage creds).
        Piece.objects.filter(pk=piece.pk).update(image_width=None, image_height=None)
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'width="4"')  # populated and emitted in this request
        piece.refresh_from_db()
        self.assertEqual((piece.image_width, piece.image_height), (4, 4))  # and persisted

    def test_view_with_missing_image_file_degrades_gracefully(self):
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        piece.image.storage.delete(piece.image.name)  # file vanished
        Piece.objects.filter(pk=piece.pk).update(image_width=None, image_height=None)
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 200)  # no 500
        piece.refresh_from_db()
        self.assertIsNone(piece.image_width)  # left null, no attributes emitted
        # Fallback branch (image present, dimensions unknown): a bare hero img
        # that still opts into the fade, with no sized/blur media box.
        self.assertContains(resp, 'data-fade')
        self.assertNotContains(resp, 'piece-figure__media')

    def test_og_image_is_an_absolute_url(self):
        # og:image must be absolute for link/social previews. The thumbnail URL
        # is relative ('/media/...') under the local filesystem backend, so the
        # template absolutizes it; under GCS it is already absolute and unchanged.
        piece = make_piece(title='Shared')
        piece.image = tiny_png()
        piece.save()
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertRegex(
            resp.content.decode(),
            r'<meta property="og:image" content="http://testserver/media/[^"]+">',
        )

    def test_og_image_omitted_without_an_image(self):
        piece = make_piece()  # no image
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'property="og:image"')

    def test_detail_back_link_carries_history_restore_hooks(self):
        piece = make_piece(title='Wayfinding')
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'data-back-link')  # JS hook for history.back()
        self.assertContains(resp, reverse('art:artists'))  # a listing path JS recognises
        self.assertContains(resp, reverse('art:location'))
        self.assertContains(resp, 'js/back-link.js')  # enhancement script loaded
        # The plain href stays as the no-JS / external-entry fallback.
        self.assertContains(resp, f'href="{reverse("art:index")}"')

    def test_detail_omits_site_header_but_other_pages_keep_it(self):
        piece = make_piece(title='Roomy')
        detail = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(detail, 'class="site-header"')  # removed to give the image room
        index = self.client.get(reverse('art:index'))
        self.assertContains(index, 'class="site-header"')  # still present elsewhere

    def test_private_notes_never_leak_publicly(self):
        piece = make_piece(notes='Visible provenance', notes_private='SECRET-CONDITION-NOTE')
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'Visible provenance')
        self.assertNotContains(resp, 'SECRET-CONDITION-NOTE')


class PublicMediaInvariantTests(TestCase):
    """Invariant: collection media (piece images, artist portraits) is PUBLIC —
    served to anyone, with no per-image gating. The ONLY private media is the
    staff-only PieceDocument, in its own private store (test_documents.py + the
    gate matrix in test_security.py). This is the complement of
    test_private_notes_never_leak_publicly: it guards against media being
    accidentally hidden from anonymous visitors. (Decision: all media is public;
    EXIF is stripped on upload — see test_images.py.)"""

    def test_piece_image_original_is_served_to_anonymous_visitors(self):
        piece = make_piece(title='With Image')
        piece.image = tiny_png()
        piece.save()
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))  # anonymous
        self.assertEqual(resp.status_code, 200)
        # Pin the MAIN figure <img> to the unprocessed original (not a bare
        # substring — the og:image thumbnail URL contains the original URL too).
        self.assertContains(resp, f'<img src="{piece.image.url}" alt=')

    def test_artist_portrait_is_shown_to_anonymous_visitors(self):
        artist = make_artist()
        artist.portrait = tiny_png()
        artist.save()
        piece = make_piece(artist=artist)
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertContains(resp, 'artist-portrait')  # portrait rendered publicly

    def test_uploaded_filename_stem_does_not_leak_in_public_urls(self):
        # The stored name (hence the public image + thumbnail URL) must not echo
        # the client filename, which can encode location/date/people.
        piece = make_piece(title='Named Upload')
        piece.image = tiny_png(name='Marthas_Vineyard_2019_kids.png')
        piece.save()
        self.assertNotIn('Marthas_Vineyard_2019_kids', piece.image.name)
        detail = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        index = self.client.get(reverse('art:index'))  # renders the thumbnail URL
        self.assertNotContains(detail, 'Marthas_Vineyard_2019_kids')
        self.assertNotContains(index, 'Marthas_Vineyard_2019_kids')


class HealthzTests(TestCase):
    def test_healthz_is_a_cheap_200_touching_no_db(self):
        with self.assertNumQueries(0):  # liveness only — never hits the DB
            resp = self.client.get(reverse('art:healthz'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'ok')
        self.assertIn('no-cache', resp.headers['Cache-Control'])


class SiteSettingsRenderingTests(TestCase):
    def test_defaults_render_when_unset(self):
        resp = self.client.get(reverse('art:index'))
        self.assertContains(resp, 'The Collection')
        self.assertContains(resp, 'Pieces are from a private collection')

    def test_configured_name_and_footer_render(self):
        s = SiteSettings.load()
        s.site_name = 'Demo Collection'
        s.footer_text = 'My custom footer.'
        s.save()
        resp = self.client.get(reverse('art:index'))
        self.assertContains(resp, 'Demo Collection')
        self.assertContains(resp, 'My custom footer.')
        self.assertNotContains(resp, 'Pieces are from a private collection')


class ThemeToggleTests(TestCase):
    """The theme switch is wired into the public chrome and degrades to the
    OS-driven default without JS (the control is hidden until theme.js runs)."""

    def test_public_page_wires_the_theme_toggle(self):
        resp = self.client.get(reverse('art:index'))
        self.assertContains(resp, 'data-theme-toggle')
        self.assertContains(resp, 'art/js/theme.js')
        # The pre-paint, no-flash init script lives inline in the <head>.
        self.assertContains(resp, "localStorage.getItem('theme')")

    def test_toggle_is_hidden_until_js_enables_it(self):
        # The button carries `hidden` so no-JS visitors never see a dead control.
        resp = self.client.get(reverse('art:index'))
        self.assertRegex(resp.content.decode(), r'data-theme-toggle[^>]*\shidden')


class NfcTaggingViewTests(TestCase):
    """`?from=tag` marks a piece tagged — but only for staff."""

    def _url(self, piece):
        return reverse('art:piece', kwargs={'slug': piece.slug})

    def _messages(self, resp):
        return [str(m) for m in get_messages(resp.wsgi_request)]

    def test_staff_from_tag_marks_tagged_and_redirects(self):
        piece = make_piece(tagged=False)
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(piece), {'from': 'tag'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self._url(piece))  # redirect drops the query
        piece.refresh_from_db()
        self.assertTrue(piece.tagged)
        self.assertTrue(any('now marked as tagged' in m for m in self._messages(resp)))

    def test_staff_from_tag_on_already_tagged_still_redirects(self):
        piece = make_piece(tagged=True)
        self.client.force_login(make_staff())
        resp = self.client.get(self._url(piece), {'from': 'tag'})
        self.assertEqual(resp.status_code, 302)
        piece.refresh_from_db()
        self.assertTrue(piece.tagged)
        self.assertTrue(any('already' in m.lower() for m in self._messages(resp)))

    def test_anonymous_from_tag_does_not_mutate_but_hints_to_sign_in(self):
        piece = make_piece(tagged=False)
        resp = self.client.get(self._url(piece), {'from': 'tag'})
        self.assertEqual(resp.status_code, 200)  # rendered, not redirected
        piece.refresh_from_db()
        self.assertFalse(piece.tagged)
        self.assertTrue(any('Sign in' in m for m in self._messages(resp)))

    def test_non_staff_from_tag_does_not_mutate(self):
        piece = make_piece(tagged=False)
        self.client.force_login(make_user())
        resp = self.client.get(self._url(piece), {'from': 'tag'})
        self.assertEqual(resp.status_code, 200)
        piece.refresh_from_db()
        self.assertFalse(piece.tagged)
        # authenticated non-staff isn't anonymous, so no sign-in hint
        self.assertFalse(any('Sign in' in m for m in self._messages(resp)))
