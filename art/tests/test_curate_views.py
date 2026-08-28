"""Tests for the /curate/ admin (art/views_curate.py).

These all run as an authenticated staff user; the gate itself is covered in
test_security.py.
"""

import json

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from art.models import Artist, Location, Medium, Piece, SiteSettings

from .factories import make_artist, make_document, make_location, make_medium, make_piece, make_staff, tiny_png


class CurateTestCase(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)

    def messages_for(self, response):
        """Flash messages queued by the request that produced `response`."""
        return [str(m) for m in get_messages(response.wsgi_request)]


class CurateRootTests(CurateTestCase):
    def test_root_redirects_to_pieces(self):
        resp = self.client.get(reverse('art:curate:home'))
        self.assertRedirects(resp, reverse('art:curate:piece-list'))

    def test_topbar_brand_is_site_name_linking_to_public_site(self):
        resp = self.client.get(reverse('art:curate:piece-list'))
        self.assertContains(resp, f'class="curate-brand" href="{reverse("art:index")}"')
        self.assertContains(resp, 'The Collection')  # the site name, shown as the brand
        self.assertNotContains(resp, 'curate-brand__sub')  # old "Curate <site name>" markup gone

    def test_topbar_wires_the_theme_toggle(self):
        # The curate admin shares the theme switch (and the no-flash init).
        resp = self.client.get(reverse('art:curate:piece-list'))
        self.assertContains(resp, 'data-theme-toggle')
        self.assertContains(resp, 'art/js/theme.js')
        self.assertContains(resp, "localStorage.getItem('theme')")


class PieceListTests(CurateTestCase):
    def setUp(self):
        super().setUp()
        self.artist = make_artist(name='Frida Kahlo')
        self.other_artist = make_artist(name='Diego Rivera')
        self.loc = make_location(description='Studio')
        self.medium = make_medium(description='Oil')

    def test_pagination_48_per_page(self):
        for i in range(50):
            make_piece(title=f'Piece {i:02d}', artist=self.artist, location=self.loc)
        page1 = self.client.get(reverse('art:curate:piece-list'))
        self.assertEqual(len(page1.context['pieces']), 48)
        self.assertTrue(page1.context['is_paginated'])
        page2 = self.client.get(reverse('art:curate:piece-list'), {'page': 2})
        self.assertEqual(len(page2.context['pieces']), 2)

    def test_search_by_title(self):
        match = make_piece(title='Sunflower Field', artist=self.artist, location=self.loc)
        make_piece(title='Mountains', artist=self.artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'q': 'sunflower'})
        self.assertEqual([p.pk for p in resp.context['pieces']], [match.pk])
        self.assertTrue(resp.context['is_filtered'])

    def test_search_by_artist_name(self):
        gogh = make_artist(name='Vincent van Gogh')
        match = make_piece(title='Irises', artist=gogh, location=self.loc)
        make_piece(title='Mountains', artist=self.other_artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'q': 'van gogh'})
        self.assertEqual([p.pk for p in resp.context['pieces']], [match.pk])

    def test_search_by_notes(self):
        match = make_piece(title='Untitled', artist=self.artist, location=self.loc, notes='a gift from the artist')
        make_piece(title='Mountains', artist=self.artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'q': 'gift'})
        self.assertEqual([p.pk for p in resp.context['pieces']], [match.pk])

    def test_search_strips_nul_bytes_then_matches(self):
        # The NUL is scrubbed *before* the query, so 'Cl\x00ean' must still find
        # 'Clean'. Asserting the match (not just HTTP 200) actually exercises the
        # scrub — a status-only check would pass even with the scrub removed.
        match = make_piece(title='Clean', artist=self.artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'q': 'Cl\x00ean'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([p.pk for p in resp.context['pieces']], [match.pk])

    def test_filter_by_artist_uuid(self):
        make_piece(title='A', artist=self.artist, location=self.loc)
        make_piece(title='B', artist=self.other_artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'artist': str(self.artist.id)})
        self.assertEqual(resp.context['paginator'].count, 1)

    def test_invalid_uuid_filter_is_ignored(self):
        make_piece(title='A', artist=self.artist, location=self.loc)
        make_piece(title='B', artist=self.other_artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'artist': 'not-a-uuid'})
        self.assertEqual(resp.context['paginator'].count, 2)  # filter dropped

    def test_tagged_filter(self):
        make_piece(title='Done', artist=self.artist, location=self.loc, tagged=True)
        make_piece(title='Todo', artist=self.artist, location=self.loc, tagged=False)
        yes = self.client.get(reverse('art:curate:piece-list'), {'tagged': 'yes'})
        no = self.client.get(reverse('art:curate:piece-list'), {'tagged': 'no'})
        self.assertEqual(yes.context['paginator'].count, 1)
        self.assertEqual(no.context['paginator'].count, 1)

    def test_querystring_drops_page_param(self):
        make_piece(title='Findable', artist=self.artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-list'), {'q': 'Findable', 'page': 1})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('q=Findable', resp.context['querystring'])
        self.assertNotIn('page=', resp.context['querystring'])

    def test_querystring_drops_empty_params(self):
        make_piece(title='Findable', artist=self.artist, location=self.loc)
        resp = self.client.get(
            reverse('art:curate:piece-list'),
            {'q': 'Findable', 'artist': '', 'has_dimensions': 'no', 'has_notes': ''},
        )
        qs = resp.context['querystring']
        self.assertIn('q=Findable', qs)
        self.assertIn('has_dimensions=no', qs)
        self.assertNotIn('artist=', qs)  # empty params don't ride along
        self.assertNotIn('has_notes=', qs)

    def test_sort_recent_orders_by_date_acquired_desc(self):
        old = make_piece(title='Older', artist=self.artist, location=self.loc, date_acquired='2010-01-01')
        new = make_piece(title='Newer', artist=self.artist, location=self.loc, date_acquired='2024-01-01')
        pieces = list(self.client.get(reverse('art:curate:piece-list'), {'sort': 'recent'}).context['pieces'])
        self.assertLess(pieces.index(new), pieces.index(old))

    def test_default_sort_is_artist_then_title(self):
        b = make_piece(title='Z', artist=make_artist(name='Aaron'), location=self.loc)
        a = make_piece(title='A', artist=make_artist(name='Zoe'), location=self.loc)
        pieces = list(self.client.get(reverse('art:curate:piece-list')).context['pieces'])
        self.assertLess(pieces.index(b), pieces.index(a))  # Aaron before Zoe

    def test_sort_added_orders_by_created_desc(self):
        import datetime

        first = make_piece(title='First in', artist=self.artist, location=self.loc)
        second = make_piece(title='Second in', artist=self.artist, location=self.loc)
        # auto_now_add stamps both at ~now; pin distinct values to assert order.
        Piece.objects.filter(pk=first.pk).update(created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
        Piece.objects.filter(pk=second.pk).update(created=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC))
        pieces = list(self.client.get(reverse('art:curate:piece-list'), {'sort': 'added'}).context['pieces'])
        self.assertLess(pieces.index(second), pieces.index(first))

    def test_unknown_sort_falls_back_to_default(self):
        # Two distinct artists so the fallback's ORDER is observable, not just
        # its status. Titles are made to DISAGREE with artist order: a title-only
        # fallback would put Frida's 'A' before Diego's 'Z', the opposite of the
        # artist-A-Z default — so the order alone distinguishes the two.
        frida = make_piece(title='A', artist=self.artist, location=self.loc)  # artist 'Frida Kahlo'
        diego = make_piece(title='Z', artist=self.other_artist, location=self.loc)  # 'Diego Rivera'
        resp = self.client.get(reverse('art:curate:piece-list'), {'sort': 'bogus'})
        self.assertEqual(resp.status_code, 200)
        pieces = list(resp.context['pieces'])
        self.assertLess(pieces.index(diego), pieces.index(frida))  # Diego before Frida (artist A-Z default)

    def test_dropdown_keeps_selected_even_with_no_pieces(self):
        empty = make_artist(name='Has No Pieces')
        resp = self.client.get(reverse('art:curate:piece-list'), {'artist': str(empty.id)})
        self.assertIn(empty, list(resp.context['artists']))


class PieceAdvancedFilterTests(CurateTestCase):
    """The "More" panel — presence (Present / Absent) filters for optional fields."""

    def setUp(self):
        super().setUp()
        self.artist = make_artist()
        self.loc = make_location()

    def _slugs(self, **params):
        resp = self.client.get(reverse('art:curate:piece-list'), params)
        return resp, [p.slug for p in resp.context['pieces']]

    def test_filter_missing_private_notes(self):
        documented = make_piece(title='Documented', artist=self.artist, location=self.loc, notes_private='secret')
        bare = make_piece(title='Bare', artist=self.artist, location=self.loc)
        resp, slugs = self._slugs(has_notes_private='no')
        self.assertIn(bare.slug, slugs)
        self.assertNotIn(documented.slug, slugs)
        self.assertTrue(resp.context['is_filtered'])
        self.assertTrue(resp.context['advanced_active'])

    def test_filter_has_dimensions(self):
        sized = make_piece(title='Sized', artist=self.artist, location=self.loc, width='10', height='20')
        unsized = make_piece(title='Unsized', artist=self.artist, location=self.loc)
        _, slugs = self._slugs(has_dimensions='yes')
        self.assertIn(sized.slug, slugs)
        self.assertNotIn(unsized.slug, slugs)

    def test_filter_missing_dimensions(self):
        sized = make_piece(title='Sized', artist=self.artist, location=self.loc, width='10', height='20')
        unsized = make_piece(title='Unsized', artist=self.artist, location=self.loc)
        _, slugs = self._slugs(has_dimensions='no')
        self.assertIn(unsized.slug, slugs)
        self.assertNotIn(sized.slug, slugs)

    def test_filter_missing_image(self):
        photo = make_piece(title='Has photo', artist=self.artist, location=self.loc)
        photo.image = tiny_png()
        photo.save()
        bare = make_piece(title='No photo', artist=self.artist, location=self.loc)
        _, slugs = self._slugs(has_image='no')
        self.assertIn(bare.slug, slugs)
        self.assertNotIn(photo.slug, slugs)

    def test_filter_has_documents(self):
        with_docs = make_piece(title='Has docs', artist=self.artist, location=self.loc)
        make_document(with_docs)
        make_document(with_docs, name='second.pdf')  # two docs — the row must not duplicate
        without = make_piece(title='No docs', artist=self.artist, location=self.loc)
        _, slugs = self._slugs(has_documents='yes')
        self.assertIn(with_docs.slug, slugs)
        self.assertNotIn(without.slug, slugs)
        self.assertEqual(slugs.count(with_docs.slug), 1)  # Exists subquery, not a multiplying join

    def test_filter_without_documents(self):
        with_docs = make_piece(title='Has docs', artist=self.artist, location=self.loc)
        make_document(with_docs)
        without = make_piece(title='No docs', artist=self.artist, location=self.loc)
        _, slugs = self._slugs(has_documents='no')
        self.assertIn(without.slug, slugs)
        self.assertNotIn(with_docs.slug, slugs)

    def test_unknown_presence_value_is_ignored(self):
        make_piece(artist=self.artist, location=self.loc)
        resp, _ = self._slugs(has_notes='banana')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['advanced_active'])  # a bogus value isn't "active"
        notes = next(f for f in resp.context['presence_filters'] if f['key'] == 'notes')
        self.assertEqual(notes['value'], '')  # and isn't reflected as a selection

    def test_more_toggle_and_panel_render(self):
        resp = self.client.get(reverse('art:curate:piece-list'))
        self.assertContains(resp, 'data-toggle-advanced')  # the toggle
        self.assertContains(resp, 'name="has_notes_private"')  # a presence dropdown
        self.assertContains(resp, 'name="has_dimensions"')
        self.assertContains(resp, 'name="has_documents"')
        self.assertFalse(resp.context['advanced_active'])  # closed by default
        self.assertContains(resp, '>More</button>')  # labelled "More" while closed

    def test_toggle_reads_less_when_panel_open(self):
        # With a filter active the panel renders open, so the toggle closes it.
        resp = self.client.get(reverse('art:curate:piece-list'), {'has_dimensions': 'no'})
        self.assertTrue(resp.context['advanced_active'])
        self.assertContains(resp, '>Less</button>')
        self.assertNotContains(resp, '>More</button>')

    def test_advanced_count_reflects_active_filters(self):
        # The count drives the collapsed "More (n)" label (rendered client-side).
        none = self.client.get(reverse('art:curate:piece-list'))
        self.assertEqual(none.context['advanced_count'], 0)
        two = self.client.get(reverse('art:curate:piece-list'), {'has_dimensions': 'no', 'has_notes': 'yes'})
        self.assertEqual(two.context['advanced_count'], 2)
        self.assertContains(two, 'data-active-count="2"')

    def test_presence_filters_cover_every_optional_field(self):
        """Maintenance guard: every optional, curator-settable Piece field must
        have a presence filter (or be deliberately excluded here). Adding a new
        such field to Piece without a filter fails this test."""
        from art.views_curate import PieceListView

        covered = set(PieceListView.PRESENCE_FILTERS) | {'width', 'height'}  # 'dimensions' covers both
        excluded = {
            'id',
            'slug',
            'title',
            'artist',
            'location',  # required / identity
            'tagged',  # has its own dedicated filter
            'purchase_currency',
            'dimension_unit',  # auto-defaulted, tied to price / size
            'created',
            'modified',  # auto timestamps
            'image_width',
            'image_height',  # auto, editable=False
        }
        optional = {
            f.name
            for f in Piece._meta.get_fields()
            if getattr(f, 'editable', False) and (getattr(f, 'null', False) or getattr(f, 'blank', False))
        }
        self.assertEqual(optional - covered - excluded, set())


class PrivateBadgeRenderTests(CurateTestCase):
    """The 'private' badge is actually wired into the rendered curate pages
    (template plumbing on top of the per-field unit tests in test_forms.py),
    including the staff-only Documents section."""

    def setUp(self):
        super().setUp()
        self.artist = make_artist()
        self.loc = make_location()

    def test_piece_add_form_renders_private_badges(self):
        resp = self.client.get(reverse('art:curate:piece-add'))
        self.assertContains(resp, 'curate-private-badge')

    def test_piece_edit_documents_section_is_badged(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertContains(resp, '<legend>Documents <span class="curate-private-badge"')

    def test_artist_edit_form_badges_email(self):
        resp = self.client.get(reverse('art:curate:artist-edit', kwargs={'pk': self.artist.id}))
        self.assertContains(resp, 'curate-private-badge')


class PieceCrudTests(CurateTestCase):
    def setUp(self):
        super().setUp()
        self.artist = make_artist()
        self.loc = make_location()
        self.medium = make_medium()

    def _valid_payload(self, **overrides):
        data = {
            'title': 'New Acquisition',
            'artist': str(self.artist.id),
            'location': str(self.loc.id),
            'medium': str(self.medium.id),
            'medium_details': '',
            'acquired': '',
            'website': '',
            'notes': '',
            'notes_private': '',
            'date_acquired': '',
            'purchase_price': '',
        }
        data.update(overrides)
        return data

    def test_add_get_renders_form(self):
        resp = self.client.get(reverse('art:curate:piece-add'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['mode'], 'add')

    def test_add_post_creates_and_redirects_to_edit_page(self):
        resp = self.client.post(reverse('art:curate:piece-add'), self._valid_payload())
        piece = Piece.objects.get(title='New Acquisition')
        # Lands on the new piece's edit page (stays in the admin), with a flash.
        self.assertRedirects(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertTrue(any('Added' in m and 'New Acquisition' in m for m in self.messages_for(resp)))

    def test_add_post_save_and_add_another_returns_to_blank_form(self):
        payload = self._valid_payload(title='First of many')
        payload['_addanother'] = '1'
        resp = self.client.post(reverse('art:curate:piece-add'), payload)
        self.assertTrue(Piece.objects.filter(title='First of many').exists())
        self.assertRedirects(resp, reverse('art:curate:piece-add'))
        self.assertTrue(any('Added' in m for m in self.messages_for(resp)))

    def test_add_post_with_image_stores_uploaded_file(self):
        payload = self._valid_payload(title='With Image')
        payload['image'] = tiny_png()
        resp = self.client.post(reverse('art:curate:piece-add'), payload)
        self.assertEqual(resp.status_code, 302)
        piece = Piece.objects.get(title='With Image')
        self.assertTrue(piece.image.name.endswith('.png'))
        self.assertIn(str(piece.id), piece.image.name)  # id_prefixed_filename prefix
        self.assertEqual((piece.image_width, piece.image_height), (4, 4))  # dims captured via the view

    def test_add_post_invalid_rerenders_with_errors(self):
        resp = self.client.post(reverse('art:curate:piece-add'), self._valid_payload(title='', artist=''))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertFalse(Piece.objects.exists())

    def test_edit_prepopulates_date_acquired(self):
        """Regression guard for the date_acquired prepopulation fix."""
        piece = make_piece(artist=self.artist, location=self.loc, date_acquired='2023-05-15')
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="2023-05-15"')

    def test_edit_form_does_not_show_the_image_filename(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        piece.image = tiny_png()
        piece.save()
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        # No "Currently: <filename>" label (the thumbnail src legitimately
        # contains the name, so we only assert the user-facing label is gone).
        self.assertNotContains(resp, 'Currently')
        self.assertContains(resp, 'type="file"')  # upload control still present

    def test_edit_form_has_no_clear_checkbox(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        piece.image = tiny_png()
        piece.save()
        resp = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'image-clear')  # no clear checkbox
        self.assertNotContains(resp, 'Clear image')

    def test_edit_without_new_upload_keeps_the_existing_image(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        piece.image = tiny_png()
        piece.save()
        original = piece.image.name
        # editing other fields, no new file: the image must be kept (not cleared)
        resp = self.client.post(
            reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}),
            self._valid_payload(title='Renamed but keep image'),
        )
        # Assert the edit actually SUCCEEDED first — a broken save re-renders at
        # 200 and saves nothing, which also leaves image.name unchanged. Without
        # these two lines the image check can't tell "preserved" from "no-op".
        self.assertEqual(resp.status_code, 302)
        piece.refresh_from_db()
        self.assertEqual(piece.title, 'Renamed but keep image')
        self.assertEqual(piece.image.name, original)

    def test_currency_shows_default_when_unset(self):
        # A new piece and an existing piece with no currency both show the
        # configured default (not a blank "----"); the blank option is gone.
        existing = make_piece(artist=self.artist, location=self.loc)
        for url in (
            reverse('art:curate:piece-add'),
            reverse('art:curate:piece-edit', kwargs={'slug': existing.slug}),
        ):
            form = self.client.get(url).context['form']
            self.assertEqual(form['purchase_currency'].value(), SiteSettings.load().default_currency)
            self.assertNotIn('', dict(form.fields['purchase_currency'].choices))

    def test_existing_currency_and_unit_are_preserved(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        Piece.objects.filter(pk=piece.pk).update(purchase_currency='EUR', dimension_unit='cm')
        form = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug})).context['form']
        self.assertEqual(form['purchase_currency'].value(), 'EUR')  # not overridden by the default
        self.assertEqual(form['dimension_unit'].value(), 'cm')

    def test_add_post_saves_the_chosen_currency(self):
        self.client.post(
            reverse('art:curate:piece-add'),
            self._valid_payload(title='Priced', purchase_price='100.00', purchase_currency='EUR'),
        )
        self.assertEqual(Piece.objects.get(title='Priced').purchase_currency, 'EUR')

    def test_currency_renders_before_purchase_price(self):
        content = self.client.get(reverse('art:curate:piece-add')).content.decode()
        self.assertLess(content.index('id_purchase_currency'), content.index('id_purchase_price'))

    def test_invalid_currency_is_rejected(self):
        resp = self.client.post(
            reverse('art:curate:piece-add'),
            self._valid_payload(title='Bad currency', purchase_currency='ZZZ'),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertFalse(Piece.objects.filter(title='Bad currency').exists())

    def test_dimension_unit_shows_default_when_unset(self):
        existing = make_piece(artist=self.artist, location=self.loc)
        for url in (
            reverse('art:curate:piece-add'),
            reverse('art:curate:piece-edit', kwargs={'slug': existing.slug}),
        ):
            form = self.client.get(url).context['form']
            self.assertEqual(form['dimension_unit'].value(), SiteSettings.load().default_dimension_unit)
            self.assertNotIn('', dict(form.fields['dimension_unit'].choices))

    def test_add_post_saves_dimensions(self):
        self.client.post(
            reverse('art:curate:piece-add'),
            self._valid_payload(title='Sized art', width='24.5', height='36', dimension_unit='cm'),
        )
        piece = Piece.objects.get(title='Sized art')
        self.assertEqual((str(piece.width), str(piece.height), piece.dimension_unit), ('24.50', '36.00', 'cm'))

    def test_invalid_dimension_unit_is_rejected(self):
        resp = self.client.post(
            reverse('art:curate:piece-add'),
            self._valid_payload(title='Bad unit', dimension_unit='furlong'),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertFalse(Piece.objects.filter(title='Bad unit').exists())

    def test_location_renders_above_the_provenance_section(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        content = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug})).content.decode()
        self.assertLess(content.index('name="location"'), content.index('>Provenance<'))

    def test_slug_renders_above_the_provenance_section(self):
        piece = make_piece(artist=self.artist, location=self.loc)
        content = self.client.get(reverse('art:curate:piece-edit', kwargs={'slug': piece.slug})).content.decode()
        self.assertIn('curate-form__slug', content)
        self.assertLess(content.index('curate-form__slug'), content.index('>Provenance<'))

    def test_first_section_is_named_details(self):
        resp = self.client.get(reverse('art:curate:piece-add'))
        self.assertContains(resp, '<legend>Details</legend>')
        self.assertNotContains(resp, '<legend>Identity</legend>')

    def test_edit_post_updates_and_stays_on_edit_page(self):
        piece = make_piece(title='Before', artist=self.artist, location=self.loc)
        resp = self.client.post(
            reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}),
            self._valid_payload(title='After'),
        )
        piece.refresh_from_db()
        self.assertEqual(piece.title, 'After')
        # slug is stable, so editing returns to the same edit page
        self.assertRedirects(resp, reverse('art:curate:piece-edit', kwargs={'slug': piece.slug}))
        self.assertTrue(any('Saved changes' in m for m in self.messages_for(resp)))

    def test_delete_confirm_then_delete(self):
        piece = make_piece(title='Doomed', artist=self.artist, location=self.loc)
        url = reverse('art:curate:piece-delete', kwargs={'slug': piece.slug})
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url)
        self.assertRedirects(resp, reverse('art:curate:piece-list'))
        self.assertFalse(Piece.objects.filter(pk=piece.pk).exists())
        self.assertTrue(any('Deleted' in m and 'Doomed' in m for m in self.messages_for(resp)))


class InlineCreateTests(CurateTestCase):
    def test_get_renders_modal(self):
        resp = self.client.get(reverse('art:curate:medium-inline-add'), {'target': 'id_medium'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'New medium')
        self.assertContains(resp, 'target=id_medium')  # propagated into submit_url

    def test_valid_post_creates_and_fires_hx_trigger(self):
        resp = self.client.post(
            reverse('art:curate:medium-inline-add') + '?target=id_medium',
            {'description': 'Linocut'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'')  # empty body closes the modal
        medium = Medium.objects.get(description='Linocut')
        trigger = json.loads(resp['HX-Trigger'])
        self.assertEqual(
            trigger['modelCreated'],
            {
                'select_id': 'id_medium',
                'id': str(medium.id),
                'name': 'Linocut',
            },
        )

    def test_artist_inline_uses_name_attribute(self):
        resp = self.client.post(
            reverse('art:curate:artist-inline-add') + '?target=id_artist',
            {'name': 'Remedios Varo'},
        )
        trigger = json.loads(resp['HX-Trigger'])
        self.assertEqual(trigger['modelCreated']['name'], 'Remedios Varo')
        self.assertTrue(Artist.objects.filter(name='Remedios Varo').exists())

    def test_artist_inline_modal_renders_the_full_form_as_multipart(self):
        resp = self.client.get(reverse('art:curate:artist-inline-add'), {'target': 'id_artist'})
        self.assertContains(resp, 'enctype="multipart/form-data"')
        self.assertContains(resp, 'hx-encoding="multipart/form-data"')  # so htmx sends the file
        # ids are namespaced so the modal's website/notes don't clash with the piece form's
        for field_id in (
            'id_inline_name',
            'id_inline_portrait',
            'id_inline_website',
            'id_inline_instagram',
            'id_inline_email',
            'id_inline_notes',
        ):
            self.assertContains(resp, field_id)
        # ...and the bare ids that WOULD collide with the piece form's own
        # website/notes must be gone — the direct guard against a namespacing regression.
        for bare_id in ('id_website', 'id_notes', 'id_email'):
            self.assertNotContains(resp, bare_id)

    def test_artist_inline_create_saves_all_fields_including_portrait(self):
        resp = self.client.post(
            reverse('art:curate:artist-inline-add') + '?target=id_artist',
            {
                'name': 'Hilma af Klint',
                'website': 'https://example.com/hilma',
                'instagram': 'https://instagram.com/hilma',
                'email': 'hilma@example.com',
                'notes': 'Pioneer of abstract art.',
                'portrait': tiny_png(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'')  # success closes the modal
        artist = Artist.objects.get(name='Hilma af Klint')
        self.assertEqual(artist.website, 'https://example.com/hilma')
        self.assertEqual(artist.email, 'hilma@example.com')
        self.assertEqual(artist.instagram, 'https://instagram.com/hilma')
        self.assertIn('Pioneer', artist.notes)
        self.assertTrue(artist.portrait)  # the uploaded file was saved
        self.assertEqual(json.loads(resp['HX-Trigger'])['modelCreated']['name'], 'Hilma af Klint')

    def test_location_inline_get_and_create(self):
        # The shared InlineCreateView path (now request.FILES + auto_id) must work
        # for the file-less location modal too, not just medium.
        get = self.client.get(reverse('art:curate:location-inline-add'), {'target': 'id_location'})
        self.assertEqual(get.status_code, 200)
        self.assertContains(get, 'New location')
        resp = self.client.post(
            reverse('art:curate:location-inline-add') + '?target=id_location',
            {'description': 'Hallway'},
        )
        self.assertEqual(resp.status_code, 200)
        location = Location.objects.get(description='Hallway')
        self.assertEqual(
            json.loads(resp['HX-Trigger'])['modelCreated'],
            {'select_id': 'id_location', 'id': str(location.id), 'name': 'Hallway'},
        )

    def test_invalid_post_returns_200_without_trigger(self):
        resp = self.client.post(
            reverse('art:curate:medium-inline-add') + '?target=id_medium',
            {'description': ''},
        )
        self.assertEqual(resp.status_code, 200)  # NOT 4xx — htmx must swap it
        self.assertNotIn('HX-Trigger', resp)
        self.assertFalse(Medium.objects.exists())


class LookupCrudTests(CurateTestCase):
    """Artist / Medium / Location share one CRUD shape; exercise each."""

    def test_list_shows_piece_counts(self):
        artist = make_artist()
        make_piece(artist=artist)
        resp = self.client.get(reverse('art:curate:artist-list'))
        self.assertEqual(resp.status_code, 200)
        listed = {a.pk: a for a in resp.context['artists']}
        self.assertEqual(listed[artist.pk].piece_count, 1)

    def test_list_search_filters_by_name(self):
        make_medium(description='Oil on canvas')
        make_medium(description='Watercolour')
        resp = self.client.get(reverse('art:curate:medium-list'), {'q': 'water'})
        self.assertEqual([m.description for m in resp.context['mediums']], ['Watercolour'])
        self.assertTrue(resp.context['is_filtered'])

    def test_list_sort_by_piece_count_both_directions(self):
        popular = make_medium(description='Popular')
        rare = make_medium(description='Rare')
        make_piece(medium=popular)
        make_piece(medium=popular)
        make_piece(medium=rare)
        most = list(self.client.get(reverse('art:curate:medium-list'), {'sort': 'most'}).context['mediums'])
        self.assertLess(most.index(popular), most.index(rare))
        fewest = list(self.client.get(reverse('art:curate:medium-list'), {'sort': 'fewest'}).context['mediums'])
        self.assertLess(fewest.index(rare), fewest.index(popular))

    def test_list_paginates(self):
        for i in range(55):
            Location.objects.create(description=f'Room {i:02d}')
        page1 = self.client.get(reverse('art:curate:location-list'))
        self.assertTrue(page1.context['is_paginated'])
        self.assertEqual(len(page1.context['locations']), 50)

    def test_medium_create_edit_delete(self):
        # create
        resp = self.client.post(reverse('art:curate:medium-add'), {'description': 'Gouache'})
        self.assertRedirects(resp, reverse('art:curate:medium-list'))
        medium = Medium.objects.get(description='Gouache')
        self.assertTrue(any('Added medium' in m and 'Gouache' in m for m in self.messages_for(resp)))
        # edit
        resp = self.client.post(
            reverse('art:curate:medium-edit', kwargs={'pk': medium.pk}),
            {'description': 'Gouache on paper'},
        )
        self.assertRedirects(resp, reverse('art:curate:medium-list'))
        medium.refresh_from_db()
        self.assertEqual(medium.description, 'Gouache on paper')
        self.assertTrue(any('Saved changes to medium' in m for m in self.messages_for(resp)))
        # delete
        resp = self.client.post(reverse('art:curate:medium-delete', kwargs={'pk': medium.pk}))
        self.assertRedirects(resp, reverse('art:curate:medium-list'))
        self.assertFalse(Medium.objects.exists())
        self.assertTrue(any('Deleted medium' in m for m in self.messages_for(resp)))

    def test_location_create(self):
        resp = self.client.post(reverse('art:curate:location-add'), {'description': 'Library'})
        self.assertRedirects(resp, reverse('art:curate:location-list'))
        self.assertTrue(Location.objects.filter(description='Library').exists())

    def test_artist_create_and_edit_form_prepopulated(self):
        resp = self.client.post(
            reverse('art:curate:artist-add'),
            {'name': 'Leonora Carrington', 'website': '', 'instagram': '', 'email': '', 'notes': ''},
        )
        self.assertRedirects(resp, reverse('art:curate:artist-list'))
        artist = Artist.objects.get(name='Leonora Carrington')
        resp = self.client.get(reverse('art:curate:artist-edit', kwargs={'pk': artist.pk}))
        self.assertContains(resp, 'Leonora Carrington')
        self.assertEqual(resp.context['mode'], 'edit')

    def test_artist_edit_shows_portrait_preview(self):
        artist = make_artist()
        artist.portrait = tiny_png()
        artist.save()
        resp = self.client.get(reverse('art:curate:artist-edit', kwargs={'pk': artist.pk}))
        self.assertContains(resp, 'curate-dropzone__preview')
        self.assertContains(resp, 'Current portrait')  # alt text on the preview <img>

    def test_delete_confirm_reports_reference_count(self):
        medium = make_medium()
        make_piece(medium=medium)
        resp = self.client.get(reverse('art:curate:medium-delete', kwargs={'pk': medium.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['piece_count'], 1)
        self.assertEqual(resp.context['object_label'], medium.description)
        self.assertNotContains(resp, 'curate-btn--danger')  # delete button hidden while referenced

    def test_deleting_a_referenced_lookup_is_blocked(self):
        # A crafted/raced POST that bypasses the hidden button must not delete a
        # lookup that still has pieces; the view answers with an error message.
        location = make_location()
        make_piece(location=location)
        resp = self.client.post(reverse('art:curate:location-delete', kwargs={'pk': location.pk}), follow=True)
        self.assertTrue(Location.objects.filter(pk=location.pk).exists())
        self.assertTrue(any('referenced by' in m for m in self.messages_for(resp)))


class SettingsViewTests(CurateTestCase):
    def _payload(self, **overrides):
        data = {
            'site_name': 'New Name',
            'footer_text': 'New footer copy',
            'public_sort': 'acquired_desc',
            'default_currency': 'USD',
            'default_dimension_unit': 'in',
        }
        data.update(overrides)
        return data

    def test_get_shows_current_values(self):
        resp = self.client.get(reverse('art:curate:settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="The Collection"')  # default in the form field

    def test_get_shows_the_default_fields(self):
        resp = self.client.get(reverse('art:curate:settings'))
        self.assertContains(resp, 'id_default_currency')
        self.assertContains(resp, 'id_default_dimension_unit')

    def test_post_updates_the_singleton(self):
        resp = self.client.post(reverse('art:curate:settings'), self._payload())
        self.assertRedirects(resp, reverse('art:curate:settings'))
        s = SiteSettings.load()
        self.assertEqual(s.site_name, 'New Name')
        self.assertEqual(s.footer_text, 'New footer copy')
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertTrue(any('saved' in m.lower() for m in self.messages_for(resp)))

    def test_post_updates_the_piece_defaults(self):
        self.client.post(
            reverse('art:curate:settings'), self._payload(default_currency='GBP', default_dimension_unit='cm')
        )
        s = SiteSettings.load()
        self.assertEqual((s.default_currency, s.default_dimension_unit), ('GBP', 'cm'))

    def test_configured_defaults_drive_the_piece_form(self):
        # The whole point of the settings: a new piece form pre-selects them.
        self.client.post(
            reverse('art:curate:settings'), self._payload(default_currency='JPY', default_dimension_unit='cm')
        )
        form = self.client.get(reverse('art:curate:piece-add')).context['form']
        self.assertEqual(form['purchase_currency'].value(), 'JPY')
        self.assertEqual(form['dimension_unit'].value(), 'cm')

    def test_post_updates_public_sort(self):
        self.client.post(reverse('art:curate:settings'), self._payload(public_sort='title'))
        self.assertEqual(SiteSettings.load().public_sort, SiteSettings.PublicSort.TITLE)

    def test_get_shows_public_sort_field(self):
        resp = self.client.get(reverse('art:curate:settings'))
        self.assertContains(resp, 'id_public_sort')


class AuthViewTests(TestCase):
    def test_login_page_renders_for_anonymous(self):
        resp = self.client.get(reverse('art:curate:login'))
        self.assertEqual(resp.status_code, 200)

    def test_valid_login_redirects_to_pieces(self):
        make_staff(username='curator', password='s3cret')
        resp = self.client.post(reverse('art:curate:login'), {'username': 'curator', 'password': 's3cret'})
        self.assertRedirects(resp, reverse('art:curate:piece-list'), fetch_redirect_response=False)

    def test_invalid_login_rerenders_and_creates_no_session(self):
        make_staff(username='curator', password='s3cret')
        resp = self.client.post(reverse('art:curate:login'), {'username': 'curator', 'password': 'WRONG'})
        self.assertEqual(resp.status_code, 200)  # re-render, not a redirect
        self.assertNotIn('_auth_user_id', self.client.session)  # not logged in

    def test_authenticated_user_redirected_away_from_login(self):
        self.client.force_login(make_staff())
        resp = self.client.get(reverse('art:curate:login'))
        self.assertEqual(resp.status_code, 302)

    def test_logout_redirects_to_login(self):
        self.client.force_login(make_staff())
        resp = self.client.post(reverse('art:curate:logout'))
        self.assertRedirects(resp, reverse('art:curate:login'), fetch_redirect_response=False)


class PieceUploadResilienceTests(CurateTestCase):
    """A chosen image must not be silently lost when a piece save fails
    validation (regression: see the dropzone preview / novalidate interplay)."""

    def test_add_form_omits_novalidate(self):
        # Without novalidate the browser blocks an empty-required submit before
        # it round-trips, so the file input keeps the chosen image.
        resp = self.client.get(reverse('art:curate:piece-add'))
        self.assertNotContains(resp, 'novalidate')

    def test_invalid_add_with_image_prompts_reselect_not_a_fake_preview(self):
        resp = self.client.post(reverse('art:curate:piece-add'), {'title': '', 'image': tiny_png()})
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors, not saved
        self.assertEqual(Piece.objects.count(), 0)
        self.assertContains(resp, 'Re-select your image')
        self.assertNotContains(resp, 'alt="Current image"')  # no misleading thumbnail
