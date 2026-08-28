import logging
import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.template.defaultfilters import slugify
from easy_thumbnails.fields import ThumbnailerImageField
from easy_thumbnails.files import get_thumbnailer

from .images import lqip_data_uri, strip_image_metadata

logger = logging.getLogger(__name__)


def id_prefixed_filename(instance, filename):
    """upload_to callable: store files as "<instance-uuid><ext>".

    The UUID alone guarantees uniqueness; the original client filename stem is
    dropped because it is served publicly in the image (and thumbnail) URL and
    can encode location/date/people (e.g. "marthas-vineyard-2019-kids.jpg")."""
    return f'{instance.id}{os.path.splitext(filename)[1].lower()}'


def private_document_storage():
    """The staff-only document store (STORAGES['private']). A callable so the
    field stays lazy and migrations reference it by import path."""
    return storages['private']


def piece_document_path(instance, filename):
    """Private-store path for a piece document: grouped under the piece and
    prefixed with the document's own UUID so stored names never collide."""
    return f'{instance.piece_id}/{instance.id}-{filename}'


def piece_document_thumb_path(instance, filename):
    """Private-store path for a document's generated thumbnail."""
    return f'{instance.piece_id}/thumbs/{instance.id}.jpg'


# ISO 4217 codes offered for a piece's purchase-price currency.
CURRENCY_CHOICES = [(c, c) for c in ('USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'CNY', 'INR', 'NZD', 'SEK')]

# Units for a piece's physical dimensions.
DIMENSION_UNIT_CHOICES = [('in', 'in'), ('cm', 'cm')]

# Dimensions and prices can't be negative. Enforced server-side here; the form
# widgets only carry a client-side min='0' that a non-browser submit can ignore.
_NON_NEGATIVE = [MinValueValidator(Decimal('0'))]


class Artist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    portrait = ThumbnailerImageField(upload_to=id_prefixed_filename, null=True, blank=True)
    website = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Strip EXIF (GPS/camera/timestamp) from a freshly-uploaded portrait —
        # it's served publicly. No-op for an unchanged stored file.
        strip_image_metadata(self.portrait)
        # Replacing the portrait shouldn't orphan the previous file + thumbnails.
        prior = None if self._state.adding else type(self).objects.filter(pk=self.pk).first()
        super().save(*args, **kwargs)
        _cleanup_replaced_image(prior, self, 'portrait')

    class Meta:
        ordering = ['name']


class Medium(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    description = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    description = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.description

    class Meta:
        ordering = ['description']


class Piece(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    image = ThumbnailerImageField(upload_to=id_prefixed_filename, null=True, blank=True)
    # Captured from the upload so the detail page can reserve the image's box
    # (no layout shift). Nullable for pieces with no image / predating this.
    image_width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    image_height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    # A tiny blurred placeholder (base64 JPEG data URI) inlined into the detail
    # page and shown in the reserved box while the full-resolution original
    # downloads. Generated from the upload alongside the dimensions above; '' for
    # pieces with no image, predating this, or whose placeholder couldn't be made.
    image_lqip = models.TextField(blank=True, default='', editable=False)
    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=1024, unique=True, null=False)
    # PROTECT on the parents a piece can't lose: deleting an artist/location
    # that still has pieces would otherwise cascade-delete the artwork records
    # (and their stored images). A medium is optional on a piece, but deleting
    # one in use shouldn't silently rewrite those pieces either — block it and
    # make the curator reassign first. The curate delete view turns the raised
    # ProtectedError into a friendly "reassign these N pieces first" message.
    artist = models.ForeignKey(Artist, on_delete=models.PROTECT)
    medium = models.ForeignKey(Medium, on_delete=models.PROTECT, null=True, blank=True)
    medium_details = models.CharField(max_length=500, blank=True)
    # Physical size of the artwork itself (distinct from image_width/height).
    # Nullable so existing pieces stay unknown; the unit defaults from settings
    # for new pieces (default='' here so existing rows aren't fabricated one).
    width = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=_NON_NEGATIVE)
    height = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=_NON_NEGATIVE)
    dimension_unit = models.CharField(max_length=2, blank=True, default='', choices=DIMENSION_UNIT_CHOICES)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    date_acquired = models.DateField(null=True, blank=True)
    acquired = models.CharField(max_length=500, blank=True)
    purchase_price = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, validators=_NON_NEGATIVE
    )
    # default='' so existing rows aren't fabricated a currency; new pieces get
    # the settings default applied as the form's initial value.
    purchase_currency = models.CharField(max_length=3, blank=True, default='', choices=CURRENCY_CHOICES)
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    notes_private = models.TextField(blank=True, verbose_name='Private notes')
    tagged = models.BooleanField(default=False)
    # Nullable: pieces predating these columns have a genuinely unknown
    # add/edit time rather than a fabricated one.
    created = models.DateTimeField(auto_now_add=True, null=True)
    modified = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return self.title

    @property
    def dimensions(self):
        """Physical size for display, e.g. '24.5 × 36 cm'; '' when unset.

        Trailing zeros are stripped (24.50 → 24.5, 36.00 → 36). normalize()
        alone would render 36.00 as '3.6E+1', so format with ':f' to force
        plain notation.
        """
        sizes = [s for s in (self.width, self.height) if s is not None]
        if not sizes:
            return ''
        # str() first: a DB-loaded value is already Decimal, but a freshly
        # assigned one may be a str — Decimal(str(...)) handles both.
        measure = ' × '.join(f'{Decimal(str(s)).normalize():f}' for s in sizes)
        return f'{measure} {self.dimension_unit}' if self.dimension_unit else measure

    class Meta:
        ordering = ['title']

    def save(self, *args, **kwargs):
        # The slug is generated once, on first save, then kept stable (it is a
        # public URL). Collisions are resolved by querying for a free suffix
        # rather than by catching IntegrityError from super().save() — that way
        # a genuine database error propagates instead of being misread as a
        # slug clash and retried. (Not guarded against two pieces with the same
        # artist+title being created concurrently, which a single curator won't
        # hit; revisit if writes ever become concurrent.)
        if not self.slug:
            self.slug = self._unique_slug()
        # Strip EXIF (GPS/camera/timestamp) from a freshly-uploaded image — it's
        # served publicly — and bake in orientation BEFORE capturing dimensions
        # so the reserved box matches the displayed pixels. No-op once committed.
        strip_image_metadata(self.image)
        self._set_image_dimensions()
        self._set_image_lqip()
        prior = None if self._state.adding else type(self).objects.filter(pk=self.pk).first()
        super().save(*args, **kwargs)
        _cleanup_replaced_image(prior, self, 'image')

    def _set_image_dimensions(self):
        # Read dimensions only from a freshly uploaded (uncommitted) file, so a
        # normal save never re-opens the stored image and a missing file can't
        # break a save. A cleared image resets them.
        image = self.image
        if not image:
            self.image_width = self.image_height = None
        elif not getattr(image, '_committed', True):
            self.image_width, self.image_height = image.width, image.height

    def _set_image_lqip(self):
        # Generate the blur-up placeholder from a freshly uploaded (uncommitted,
        # already-stripped) file only — mirrors _set_image_dimensions so a normal
        # save never re-opens the stored image. A cleared image resets it.
        image = self.image
        if not image:
            self.image_lqip = ''
        elif not getattr(image, '_committed', True):
            self.image_lqip = lqip_data_uri(image)

    def ensure_image_dimensions(self):
        """Read-time counterpart to _set_image_dimensions: backfill the size of
        an already-stored image the first time its piece is viewed, for images
        uploaded before the dimensions were captured. A cheap header read (not a
        full decode), at most once per piece, then cached. Unlike the upload-time
        setter this opens the stored file, so it tolerates an unreadable/missing
        one by leaving the size null (the template falls back).

        The blur-up placeholder (image_lqip) is deliberately NOT generated here:
        building it needs a full-resolution pixel decode, and doing that on the
        request path — concurrent anonymous detail views of full-res originals —
        exhausted a small server's memory (OOM-killed gunicorn). image_lqip is
        populated at upload and by the `backfill_image_lqip` /
        `backfill_strip_image_metadata` commands instead."""
        if not self.image or (self.image_width and self.image_height):
            return
        try:
            self.image_width, self.image_height = self.image.width, self.image.height
        except Exception:
            return  # unreadable/missing file — leave null
        self.save(update_fields=['image_width', 'image_height'])

    def _unique_slug(self):
        base_slug = '-'.join((slugify(self.artist.name), slugify(self.title)))
        slug, counter = base_slug, 0
        others = Piece.objects.exclude(pk=self.pk)
        while others.filter(slug=slug).exists():
            counter += 1
            slug = f'{base_slug}-{counter}'
        return slug


def _delete_image_file(fieldfile):
    """Remove an uploaded image *and its generated thumbnails* from storage
    (no-op if the field is empty). The field's own delete() only removes the
    source file; delete_thumbnails() clears the easy-thumbnails-tracked
    thumbnails too, which would otherwise be orphaned in the bucket.

    Storage errors are logged and swallowed, never raised: this runs from a
    post_delete signal (and the replace-on-save path) where the database row is
    already gone or updated, so raising would 500 an operation that effectively
    succeeded and leave the file orphaned anyway. `cleanup_orphan_images` is the
    safety net that reconciles any file a transient failure leaves behind."""
    if not fieldfile:
        return
    try:
        get_thumbnailer(fieldfile).delete_thumbnails()
        fieldfile.delete(save=False)
    except Exception:
        logger.warning('Could not delete image file %r from storage', fieldfile.name, exc_info=True)


def _cleanup_replaced_image(prior, instance, field_name):
    """Delete the previously stored file (and its thumbnails) when a save
    replaced it with a different one. `prior` is the instance as it existed in
    the database before this save, or None for a new/unchanged save. Without
    this, uploading a replacement image orphans the old source + thumbnails."""
    if prior is None:
        return
    old = getattr(prior, field_name)
    if old and old.name != getattr(instance, field_name).name:
        _delete_image_file(old)


@receiver(post_delete, sender=Piece)
def _piece_image_cleanup(sender, instance, **kwargs):
    # A deleted piece shouldn't leave its image orphaned in storage.
    _delete_image_file(instance.image)


@receiver(post_delete, sender=Artist)
def _artist_portrait_cleanup(sender, instance, **kwargs):
    _delete_image_file(instance.portrait)


def _delete_file(fieldfile):
    """Remove a plain stored file (no thumbnails). Failure-tolerant like
    _delete_image_file: a storage error must never break the delete."""
    if not fieldfile:
        return
    try:
        fieldfile.delete(save=False)
    except Exception:
        logger.warning('Could not delete file %r from storage', fieldfile.name, exc_info=True)


class PieceDocument(models.Model):
    """A staff-only file attached to a Piece — receipt, valuation, provenance,
    condition report, etc. Stored in the private store (STORAGES['private']) and
    served ONLY through the staff-gated download view; it never has a public URL.
    Deleting the piece cascades, and the post_delete signal removes the file."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    piece = models.ForeignKey(Piece, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to=piece_document_path, storage=private_document_storage)
    # Captured at upload so the list shows a real name (the stored name is
    # uuid-prefixed) and the download sets a correct content type / filename.
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveBigIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    # A small preview (image resize / PDF first page), generated best-effort and
    # stored in the SAME private store — a thumbnail of a receipt is as sensitive
    # as the receipt, so it's served only through the gated thumbnail view too.
    thumbnail = models.FileField(
        upload_to=piece_document_thumb_path, storage=private_document_storage, null=True, blank=True
    )

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.original_name or self.file.name

    def generate_thumbnail(self):
        """Render and store a thumbnail from the stored file. Best-effort: any
        failure (unreadable file, unsupported/garbled content, storage error) is
        logged and returns False, leaving the document thumbnail-less. Returns
        True if a thumbnail was produced."""
        from .thumbnails import render_thumbnail

        if not self.file:
            return False
        name = (self.original_name or self.file.name or '').lower()
        is_pdf = self.content_type == 'application/pdf' or name.endswith('.pdf')
        try:
            with self.file.open('rb') as fh:
                data = render_thumbnail(fh, is_pdf=is_pdf)
            if not data:
                return False
            self.thumbnail.save(f'{self.id}.jpg', ContentFile(data), save=False)
            self.save(update_fields=['thumbnail'])
            return True
        except Exception:
            logger.warning('Could not generate thumbnail for document %s', self.pk, exc_info=True)
            return False


@receiver(post_delete, sender=PieceDocument)
def _piece_document_cleanup(sender, instance, **kwargs):
    # A deleted document (or a piece-cascade delete) shouldn't orphan its file
    # or its generated thumbnail.
    _delete_file(instance.file)
    _delete_file(instance.thumbnail)


class SiteSettings(models.Model):
    """Curator-editable, site-wide copy. A single row (enforced in save())."""

    class PublicSort(models.TextChoices):
        ACQUIRED_DESC = 'acquired_desc', 'Date acquired, newest first'
        ACQUIRED_ASC = 'acquired_asc', 'Date acquired, oldest first'
        TITLE = 'title', 'Title (A–Z)'
        ARTIST = 'artist', 'Artist name (A–Z)'

    site_name = models.CharField(max_length=200, default='The Collection')
    footer_text = models.TextField(
        default='Pieces are from a private collection. All rights reserved by the respective artists.'
    )
    # Curator-set order for the public "All pieces" page (not visitor-selectable).
    public_sort = models.CharField(
        max_length=20,
        choices=PublicSort.choices,
        default=PublicSort.ACQUIRED_DESC,
        verbose_name='Public sort order',
        help_text='Order pieces are listed in on the public “All pieces” page.',
    )
    # Defaults pre-selected on the piece form for new (and unset) pieces.
    default_currency = models.CharField(max_length=3, default='USD', choices=CURRENCY_CHOICES)
    default_dimension_unit = models.CharField(max_length=2, default='in', choices=DIMENSION_UNIT_CHOICES)

    class Meta:
        verbose_name = 'site settings'
        verbose_name_plural = 'site settings'

    def __str__(self):
        return 'Site settings'

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce a single row
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        # The saved singleton if present, else a transient instance carrying the
        # field defaults — so the public site renders before anything is saved.
        return cls.objects.first() or cls()

    def public_ordering(self):
        """order_by() args for the public “All pieces” list, per public_sort.

        date_acquired keeps NULLs last in BOTH directions (undated pieces always
        trail), and every option tie-breaks on title so the order is stable.
        Falls back to the default if public_sort holds an unrecognised value.
        """
        title = 'title'
        orderings = {
            self.PublicSort.ACQUIRED_DESC: (models.F('date_acquired').desc(nulls_last=True), title),
            self.PublicSort.ACQUIRED_ASC: (models.F('date_acquired').asc(nulls_last=True), title),
            self.PublicSort.TITLE: (title,),
            self.PublicSort.ARTIST: ('artist__name', title),
        }
        return orderings.get(self.public_sort, orderings[self.PublicSort.ACQUIRED_DESC])
