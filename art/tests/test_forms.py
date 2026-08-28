"""Tests for art/forms.py — the field-requirement workarounds and widgets."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import modelform_factory
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from PIL import Image

from art.forms import ArtistForm, LocationForm, MediumForm, PieceForm
from art.models import Piece

from .factories import make_artist, make_location, make_medium, make_piece, tiny_png


class PieceFormTests(TestCase):
    def test_image_and_medium_are_optional(self):
        form = PieceForm()
        self.assertFalse(form.fields['image'].required)
        self.assertFalse(form.fields['medium'].required)
        self.assertEqual(form.fields['medium'].empty_label, '— none —')

    def test_core_fields_required(self):
        form = PieceForm()
        for name in ('title', 'artist', 'location'):
            self.assertTrue(form.fields[name].required, name)

    def test_date_acquired_uses_html5_date_widget(self):
        widget = PieceForm().fields['date_acquired'].widget
        self.assertEqual(widget.input_type, 'date')

    def test_valid_without_image_or_medium(self):
        artist = make_artist()
        location = make_location()
        form = PieceForm(
            data={
                'title': 'No Image Piece',
                'artist': str(artist.id),
                'location': str(location.id),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_without_required_fields(self):
        self.assertFalse(PieceForm(data={'title': ''}).is_valid())

    def test_optionality_comes_from_the_model(self):
        """image/medium optionality lives on the model (blank=True), so a bare
        ModelForm — e.g. the Django admin's — treats them as optional too,
        without any per-form override."""
        BareForm = modelform_factory(Piece, fields=['image', 'medium', 'title'])
        fields = BareForm().fields
        self.assertFalse(fields['image'].required)
        self.assertFalse(fields['medium'].required)
        self.assertTrue(fields['title'].required)

    def test_unpriced_piece_does_not_fabricate_a_currency(self):
        # A legacy piece with no price/currency, edited for an unrelated reason,
        # must not acquire a currency just because the dropdown always shows one.
        artist, location = make_artist(), make_location()
        piece = make_piece(artist=artist, location=location)
        Piece.objects.filter(pk=piece.pk).update(purchase_currency='', dimension_unit='')
        piece.refresh_from_db()
        form = PieceForm(
            data={
                'title': 'Edited title',
                'artist': str(artist.id),
                'location': str(location.id),
                'purchase_currency': 'USD',  # what the always-populated dropdown submits
                'dimension_unit': 'in',  # ditto, with no width/height set
            },
            instance=piece,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.purchase_currency, '')
        self.assertEqual(saved.dimension_unit, '')

    def test_negative_dimensions_and_price_are_rejected(self):
        artist, location = make_artist(), make_location()
        form = PieceForm(
            data={
                'title': 'Negative',
                'artist': str(artist.id),
                'location': str(location.id),
                'width': '-5',
                'height': '-3',
                'purchase_price': '-10.00',
            }
        )
        self.assertFalse(form.is_valid())
        for field in ('width', 'height', 'purchase_price'):
            self.assertIn(field, form.errors)

    def test_priced_and_sized_piece_keeps_currency_and_unit(self):
        artist, location = make_artist(), make_location()
        form = PieceForm(
            data={
                'title': 'Priced piece',
                'artist': str(artist.id),
                'location': str(location.id),
                'purchase_price': '120.00',
                'purchase_currency': 'EUR',
                'width': '30',
                'height': '40',
                'dimension_unit': 'cm',
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.purchase_currency, 'EUR')
        self.assertEqual(saved.dimension_unit, 'cm')


class ImageUploadLimitTests(TestCase):
    def _piece_data(self):
        return {
            'title': 'Upload test',
            'artist': str(make_artist().id),
            'location': str(make_location().id),
        }

    def test_normal_image_upload_is_accepted(self):
        form = PieceForm(data=self._piece_data(), files={'image': tiny_png()})
        self.assertTrue(form.is_valid(), form.errors)

    def test_unsupported_image_format_is_rejected(self):
        # BMP is decodable by Pillow but outside the strippable set, so the form
        # rejects it rather than store an un-strippable original.
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), (1, 2, 3)).save(buf, format='BMP')
        bmp = SimpleUploadedFile('x.bmp', buf.getvalue(), content_type='image/bmp')
        form = PieceForm(data=self._piece_data(), files={'image': bmp})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_renamed_jp2_is_rejected_by_sniffed_format(self):
        # A JPEG2000 (carries EXIF/XMP) renamed .jpg must still be rejected — the
        # check sniffs the real format, not the extension.
        buf = io.BytesIO()
        try:
            Image.new('RGB', (8, 8), (1, 2, 3)).save(buf, format='JPEG2000')
        except Exception:
            self.skipTest('JPEG2000 encoder unavailable')
        jp2 = SimpleUploadedFile('photo.jpg', buf.getvalue(), content_type='image/jpeg')
        form = ArtistForm(data={'name': 'X'}, files={'portrait': jp2})
        self.assertFalse(form.is_valid())
        self.assertIn('portrait', form.errors)

    @override_settings(MAX_IMAGE_UPLOAD_BYTES=10)
    def test_oversized_piece_image_is_rejected(self):
        form = PieceForm(data=self._piece_data(), files={'image': tiny_png()})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    @override_settings(MAX_IMAGE_UPLOAD_BYTES=10)
    def test_oversized_artist_portrait_is_rejected(self):
        form = ArtistForm(data={'name': 'Bob'}, files={'portrait': tiny_png()})
        self.assertFalse(form.is_valid())
        self.assertIn('portrait', form.errors)

    @override_settings(MAX_IMAGE_UPLOAD_BYTES=10)
    def test_editing_a_piece_without_reuploading_is_not_blocked(self):
        # The stored image is a FieldFile, not an upload, so the size check must
        # not fire when a curator edits a piece without changing its image.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        form = PieceForm(
            data={'title': 'Edited', 'artist': str(piece.artist_id), 'location': str(piece.location_id)},
            instance=piece,
        )
        self.assertTrue(form.is_valid(), form.errors)


class UnsavedUploadTests(TestCase):
    """form.unsaved_image flags a freshly-uploaded file that an invalid submit
    is about to drop (the browser clears <input type=file> on the reload), so the
    template can prompt a re-select instead of rendering a never-saved preview."""

    def test_true_when_upload_present_but_form_invalid(self):
        form = PieceForm(
            data={'title': '', 'artist': str(make_artist().id), 'location': str(make_location().id)},
            files={'image': tiny_png()},
        )
        self.assertFalse(form.is_valid())  # blank title
        self.assertTrue(form.unsaved_image)

    def test_false_without_an_upload(self):
        form = PieceForm(data={'title': ''})
        self.assertFalse(form.is_valid())
        self.assertFalse(form.unsaved_image)

    def test_false_for_a_committed_existing_image(self):
        # An edit that fails for an unrelated reason must keep showing the saved
        # image (it's committed and safe), not the re-select warning.
        piece = make_piece()
        piece.image = tiny_png()
        piece.save()
        form = PieceForm(data={'title': ''}, instance=piece)
        self.assertFalse(form.is_valid())
        self.assertFalse(form.unsaved_image)

    def test_false_on_an_unbound_form(self):
        self.assertFalse(PieceForm().unsaved_image)
        self.assertFalse(ArtistForm(instance=make_artist()).unsaved_image)

    def test_artist_portrait_upload_is_flagged_too(self):
        form = ArtistForm(data={'name': ''}, files={'portrait': tiny_png()})
        self.assertFalse(form.is_valid())
        self.assertTrue(form.unsaved_image)


class LookupFormTests(TestCase):
    def test_medium_and_location_labels(self):
        self.assertEqual(MediumForm().fields['description'].label, 'Medium name')
        self.assertEqual(LocationForm().fields['description'].label, 'Location name')

    def test_artist_form_fields(self):
        self.assertEqual(
            list(ArtistForm().fields),
            ['name', 'portrait', 'website', 'instagram', 'email', 'notes'],
        )

    def test_medium_form_saves(self):
        form = MediumForm(data={'description': 'Tempera'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.save().description, 'Tempera')

    def test_medium_form_rejects_duplicate_description(self):
        make_medium(description='Oil')
        form = MediumForm(data={'description': 'Oil'})
        self.assertFalse(form.is_valid())
        self.assertIn('description', form.errors)


class PrivateFieldBadgeTests(TestCase):
    """The curate form flags curator-only fields with a 'private' badge so it's
    obvious which values never reach the public site. The set IS the privacy
    boundary from CLAUDE.md; _field.html renders the badge from form.PRIVATE_FIELDS.
    These pin the set and prove the per-field rendering both ways (present for
    private fields, absent for public ones) so the cue can't silently drift."""

    BADGE = 'curate-private-badge'

    def _field_html(self, form, name):
        return render_to_string('curate/_field.html', {'field': form[name]})

    def test_piece_private_fields_are_exactly_the_curator_only_set(self):
        self.assertEqual(
            PieceForm.PRIVATE_FIELDS,
            frozenset({'purchase_price', 'purchase_currency', 'notes_private'}),
        )

    def test_artist_private_fields_are_exactly_the_curator_only_set(self):
        self.assertEqual(ArtistForm.PRIVATE_FIELDS, frozenset({'email'}))

    def test_private_fields_render_the_badge(self):
        form = PieceForm()
        for name in PieceForm.PRIVATE_FIELDS:
            self.assertIn(self.BADGE, self._field_html(form, name), name)
        self.assertIn(self.BADGE, self._field_html(ArtistForm(), 'email'))

    def test_public_fields_do_not_render_the_badge(self):
        piece = PieceForm()
        for name in ('title', 'date_acquired', 'acquired', 'website', 'notes'):
            self.assertNotIn(self.BADGE, self._field_html(piece, name), name)
        artist = ArtistForm()
        for name in ('name', 'website', 'instagram', 'notes'):
            self.assertNotIn(self.BADGE, self._field_html(artist, name), name)

    def test_form_without_private_fields_renders_no_badge(self):
        # A form with no PRIVATE_FIELDS attribute must neither error nor badge.
        self.assertNotIn(self.BADGE, self._field_html(MediumForm(), 'description'))
