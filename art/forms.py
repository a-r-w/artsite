"""ModelForms for the /curate/ admin."""

import os

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from PIL import Image

from .images import ACCEPTED_UPLOAD_FORMATS, strip_metadata_bytes
from .models import Artist, Location, Medium, Piece, SiteSettings

# Ceiling on a freshly uploaded image. Uploads are staff-only, but an unbounded
# file still has to be decoded by Pillow (dimension capture + thumbnailing) and
# stored, so a huge upload is a memory/disk foot-gun worth rejecting up front.
# Overridable via the MAX_IMAGE_UPLOAD_BYTES setting.
DEFAULT_MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024


def validate_image_upload(value):
    """Validate a freshly uploaded image: bound the size and restrict to formats
    the metadata strip can re-encode (by SNIFFED format, not extension — a
    renamed .jpg that is really JPEG2000/PSD is caught). Rejecting un-strippable
    EXIF/XMP carriers up front keeps a metadata-bearing original from ever being
    stored and served publicly. An unchanged stored image (a FieldFile, not an
    UploadedFile) passes through untouched, so editing without re-uploading is
    never blocked."""
    if isinstance(value, UploadedFile):
        cap = getattr(settings, 'MAX_IMAGE_UPLOAD_BYTES', DEFAULT_MAX_IMAGE_UPLOAD_BYTES)
        if value.size > cap:
            raise ValidationError(f'Image is too large — keep it under {cap // (1024 * 1024)} MB.')
        value.seek(0)
        try:
            with Image.open(value) as img:
                fmt = img.format
        except Exception:
            raise ValidationError('Could not read this image file.') from None
        finally:
            value.seek(0)
        if fmt not in ACCEPTED_UPLOAD_FORMATS:
            raise ValidationError(f'Unsupported image type ({fmt}). Use JPEG, PNG, WEBP, GIF, TIFF, HEIC, or AVIF.')
        # Fail closed: confirm the metadata strip actually succeeds here, so a
        # corrupt/odd image is rejected with a message rather than 500ing at save
        # or (worse) being stored with its metadata intact.
        value.seek(0)
        try:
            strip_metadata_bytes(value.read())
        except Exception:
            raise ValidationError('Could not process this image; try re-exporting it as JPEG or PNG.') from None
        finally:
            value.seek(0)
    return value


DEFAULT_MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_DOCUMENT_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic')


def validate_document_upload(upload):
    """Validate a staff-uploaded piece document: bound the size and restrict to
    a sane allowlist of extensions (receipts/scans are PDFs or images). Raises
    ValidationError; the add view surfaces the message and skips that file."""
    cap = getattr(settings, 'MAX_DOCUMENT_UPLOAD_BYTES', DEFAULT_MAX_DOCUMENT_UPLOAD_BYTES)
    if upload.size > cap:
        raise ValidationError(f'too large — keep it under {cap // (1024 * 1024)} MB')
    allowed = getattr(settings, 'ALLOWED_DOCUMENT_EXTENSIONS', DEFAULT_DOCUMENT_EXTENSIONS)
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in allowed:
        raise ValidationError(f'unsupported file type ({ext or "no extension"})')


def has_unsaved_upload(form, field_name):
    """True when a bound, invalid submit carried a freshly-uploaded file.

    The browser clears <input type="file"> on the error reload, so that upload is
    already gone — the template should prompt a re-select rather than render a
    stale preview of a file that was never saved. An unchanged stored file (a
    committed FieldFile loaded from the DB) returns False, so an edit form still
    shows its existing image.
    """
    if not (form.is_bound and form.errors):
        return False
    fieldfile = getattr(form.instance, field_name, None)
    # _committed: False for a file just assigned from an upload but not yet
    # written to storage; True for one loaded from the database.
    return bool(fieldfile) and not fieldfile._committed


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['site_name', 'footer_text', 'public_sort', 'default_currency', 'default_dimension_unit']
        labels = {
            'site_name': 'Site name',
            'footer_text': 'Public footer text',
            'default_currency': 'Default currency',
            'default_dimension_unit': 'Default dimension unit',
        }
        widgets = {'footer_text': forms.Textarea(attrs={'rows': 3})}
        help_texts = {
            'site_name': 'Shown as the site title and in the curate header.',
            'footer_text': 'Shown at the foot of every public page.',
            'default_currency': 'Pre-selected on a new piece.',
            'default_dimension_unit': 'Pre-selected on a new piece.',
        }


class ArtistForm(forms.ModelForm):
    # Curator-only fields: rendered in the curate form with a "private" badge and
    # never shown on the public site. Keep in sync with the privacy boundary in
    # CLAUDE.md and the public-leak guards in test_public_views.py.
    PRIVATE_FIELDS = frozenset({'email'})

    def clean_portrait(self):
        return validate_image_upload(self.cleaned_data.get('portrait'))

    @property
    def unsaved_image(self):
        return has_unsaved_upload(self, 'portrait')

    class Meta:
        model = Artist
        fields = ['name', 'portrait', 'website', 'instagram', 'email', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'website': forms.URLInput(attrs={'placeholder': 'https://'}),
            'instagram': forms.URLInput(attrs={'placeholder': 'https://instagram.com/…'}),
        }


class MediumForm(forms.ModelForm):
    class Meta:
        model = Medium
        fields = ['description']
        labels = {'description': 'Medium name'}


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ['description']
        labels = {'description': 'Location name'}


class PieceForm(forms.ModelForm):
    # Curator-only fields: rendered in the curate form with a "private" badge and
    # never shown on the public site (see the privacy boundary in CLAUDE.md). The
    # Documents section is the same category, badged in the template.
    PRIVATE_FIELDS = frozenset({'purchase_price', 'purchase_currency', 'notes_private'})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # image and medium are optional via the model (blank=True). Only the
        # friendlier empty label isn't derivable from the model.
        self.fields['medium'].empty_label = '— none —'
        # Currency and dimension unit always show a real value: drop the blank
        # "----" option, and pre-select the piece's value or the curator's
        # configured default when it has none — for new and existing pieces alike.
        site = SiteSettings.load()
        for name, default in (
            ('purchase_currency', site.default_currency),
            ('dimension_unit', site.default_dimension_unit),
        ):
            self.fields[name].choices = [choice for choice in self.fields[name].choices if choice[0]]
            if not self.initial.get(name):
                self.initial[name] = default

    def clean(self):
        # The currency/unit dropdowns always render a value (the blank option is
        # dropped above), so a plain save would persist one even when there's
        # nothing for it to qualify — fabricating a currency for a piece with no
        # price, or a unit for a piece with no dimensions. That defeats the
        # model's default='' intent and is exactly what bites a legacy row when
        # it's edited for an unrelated reason. Drop the orphaned value back to ''
        # so an unpriced / dimensionless piece stays genuinely unset.
        cleaned = super().clean()
        if cleaned.get('purchase_price') is None:
            cleaned['purchase_currency'] = ''
        if cleaned.get('width') is None and cleaned.get('height') is None:
            cleaned['dimension_unit'] = ''
        return cleaned

    def clean_image(self):
        return validate_image_upload(self.cleaned_data.get('image'))

    @property
    def unsaved_image(self):
        return has_unsaved_upload(self, 'image')

    class Meta:
        model = Piece
        fields = [
            'image',
            'title',
            'artist',
            'medium',
            'medium_details',
            'width',
            'height',
            'dimension_unit',
            'location',
            'date_acquired',
            'acquired',
            'purchase_price',
            'purchase_currency',
            'website',
            'notes',
            'notes_private',
            'tagged',
        ]
        widgets = {
            # Plain file input: the dropzone previews the current image, so no
            # filename or "clear" checkbox is shown (and there's no way to clear).
            'image': forms.FileInput(),
            'date_acquired': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 5}),
            'notes_private': forms.Textarea(attrs={'rows': 4}),
            'purchase_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'width': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'height': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'website': forms.URLInput(attrs={'placeholder': 'https://'}),
            'medium_details': forms.TextInput(attrs={'placeholder': ''}),
            'acquired': forms.TextInput(attrs={'placeholder': 'where / from whom'}),
        }
        labels = {'purchase_currency': 'Currency'}
        help_texts = {
            'tagged': 'Check once an NFC tag has been written for this piece.',
            'notes_private': 'Not shown on the public site — purchase context, condition, etc.',
        }
