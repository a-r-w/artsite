"""Blur-up LQIP placeholder for the detail-page hero image.

A tiny base64 JPEG is generated from each piece image at upload (and, for legacy
images, by the backfill_image_lqip / backfill_strip_image_metadata commands —
NOT on the request path, since the full-res decode it needs OOM-ed a small box),
stored in Piece.image_lqip, and inlined into the detail page so the reserved
image box shows a blurred preview while the full-resolution original downloads.
Generation is best-effort (cosmetic) and must never block a save or a view.
"""

import base64
import io

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from PIL import Image, UnidentifiedImageError

from art.images import _LQIP_MAX_EDGE, _lqip_from_bytes, lqip_data_uri
from art.models import Piece

from .factories import make_piece, tiny_png

_DATA_PREFIX = 'data:image/jpeg;base64,'


def _decode(uri):
    """The PIL image carried by an lqip data URI."""
    assert uri.startswith(_DATA_PREFIX), uri[:40]
    return Image.open(io.BytesIO(base64.b64decode(uri[len(_DATA_PREFIX) :])))


def _png_bytes(size=(800, 600), color=(120, 60, 30)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def _jpeg_with_metadata(comment=b'GEO-SECRET-COMMENT', make='SECRET-CAMERA-MAKE'):
    """A JPEG carrying a COM comment + EXIF — the kind of metadata a legacy,
    un-stripped stored image still holds when the placeholder is built from it."""
    exif = Image.Exif()
    exif[0x010F] = make  # Make
    buf = io.BytesIO()
    Image.new('RGB', (800, 600), (10, 20, 30)).save(buf, 'JPEG', comment=comment, exif=exif)
    return buf.getvalue()


class LqipGenerationTests(TestCase):
    def test_produces_a_tiny_jpeg_data_uri(self):
        uri = _lqip_from_bytes(_png_bytes((800, 600)))
        with _decode(uri) as im:
            self.assertEqual(im.format, 'JPEG')
            self.assertLessEqual(max(im.size), _LQIP_MAX_EDGE)  # downscaled
            self.assertEqual(round(im.width / im.height, 2), round(800 / 600, 2))  # aspect kept

    def test_placeholder_is_small_enough_to_inline(self):
        # A 20px JPEG should be well under ~1KB of base64 — cheap to embed.
        uri = _lqip_from_bytes(_png_bytes((4000, 3000)))
        self.assertLess(len(uri), 1500)

    def test_data_uri_has_no_attribute_breaking_characters(self):
        # It lands in an HTML style attribute inside url('...'); base64 + the
        # fixed prefix must contain no quotes/spaces/angle brackets.
        uri = _lqip_from_bytes(_png_bytes())
        for bad in ('"', "'", ' ', '<', '>'):
            self.assertNotIn(bad, uri)

    def test_lqip_data_uri_is_best_effort_on_bad_input(self):
        self.assertEqual(lqip_data_uri(None), '')  # no file
        with self.assertRaises(UnidentifiedImageError):
            _lqip_from_bytes(b'not an image')  # core raises...
        # ...but the fieldfile wrapper swallows it (cosmetic, never blocks).

    def test_placeholder_carries_no_source_metadata(self):
        # The placeholder is inlined UNescaped into the public detail page and is
        # often built (legacy self-heal / migrated files) from a still-unstripped
        # original — so it must not re-emit the source's EXIF or COM comment (a
        # geotag/owner carrier). Regression for the JPEG-comment leak.
        uri = _lqip_from_bytes(_jpeg_with_metadata())
        blob = base64.b64decode(uri[len(_DATA_PREFIX) :])
        self.assertNotIn(b'GEO-SECRET-COMMENT', blob)
        self.assertNotIn(b'SECRET-CAMERA-MAKE', blob)
        with _decode(uri) as im:
            self.assertEqual(len(im.getexif()), 0)
            self.assertNotIn('comment', im.info)


class LqipOnSaveTests(TestCase):
    def test_upload_populates_image_lqip(self):
        piece = make_piece(image=tiny_png())
        self.assertTrue(piece.image_lqip.startswith(_DATA_PREFIX))
        with _decode(piece.image_lqip) as im:
            self.assertEqual(im.format, 'JPEG')

    def test_piece_without_image_has_blank_lqip(self):
        piece = make_piece()
        self.assertEqual(piece.image_lqip, '')

    def test_clearing_the_image_resets_lqip(self):
        piece = make_piece(image=tiny_png())
        self.assertNotEqual(piece.image_lqip, '')
        piece.image = None
        piece.save()
        piece.refresh_from_db()
        self.assertEqual(piece.image_lqip, '')


class LqipBackfillTests(TestCase):
    def test_read_path_backfills_dims_but_not_the_placeholder(self):
        # ensure_image_dimensions must stay cheap: it backfills the (header-only)
        # dimensions but NOT the placeholder, whose full-res decode is kept off
        # the request path (it OOM-ed a small server).
        piece = make_piece(image=tiny_png())
        Piece.objects.filter(pk=piece.pk).update(image_lqip='', image_width=None, image_height=None)
        piece.refresh_from_db()

        piece.ensure_image_dimensions()
        piece.refresh_from_db()
        self.assertIsNotNone(piece.image_width)  # dims backfilled (cheap)
        self.assertEqual(piece.image_lqip, '')  # placeholder NOT (off read path)

    def test_ensure_is_a_noop_once_dimensions_are_present(self):
        piece = make_piece(image=tiny_png())
        with self.assertNumQueries(0):  # dimensions already set -> no read/write
            piece.ensure_image_dimensions()

    def test_backfill_command_fills_missing_placeholders(self):
        piece = make_piece(image=tiny_png())
        Piece.objects.filter(pk=piece.pk).update(image_lqip='')
        call_command('backfill_image_lqip', '--apply')
        piece.refresh_from_db()
        self.assertTrue(piece.image_lqip.startswith(_DATA_PREFIX))

    def test_backfill_command_dry_run_writes_nothing(self):
        piece = make_piece(image=tiny_png())
        Piece.objects.filter(pk=piece.pk).update(image_lqip='')
        call_command('backfill_image_lqip')  # no --apply
        piece.refresh_from_db()
        self.assertEqual(piece.image_lqip, '')


class LqipDetailRenderTests(TestCase):
    def test_detail_inlines_the_placeholder_and_fade_hook(self):
        piece = make_piece(image=tiny_png())
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'piece-figure__media')
        self.assertContains(resp, '--lqip: url(')
        self.assertContains(resp, piece.image_lqip)  # the actual data URI, unescaped
        self.assertContains(resp, 'data-fade')
        # The template comment must not leak as visible text (a multi-line {# #}
        # silently renders; use {% comment %}). Regression guard.
        self.assertNotContains(resp, 'no layout shift')

    def test_detail_without_image_has_no_media_box(self):
        piece = make_piece()
        resp = self.client.get(reverse('art:piece', kwargs={'slug': piece.slug}))
        self.assertNotContains(resp, 'piece-figure__media')
