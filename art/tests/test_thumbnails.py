"""Tests for art/thumbnails.py — best-effort image/PDF thumbnail rendering."""

import io

from django.test import SimpleTestCase
from PIL import Image

from art.thumbnails import _pdf_first_page, render_thumbnail


def _png(size=(600, 400), color=(90, 140, 210)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def _pdf(size=(300, 200)):
    # Pillow can author a 1-page PDF — a real fixture with no extra dependency.
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 90, 60)).save(buf, 'PDF')
    return buf.getvalue()


def _heic(size=(240, 160)):
    # Requires the HEIF opener registered in ArtConfig.ready() (done at startup).
    buf = io.BytesIO()
    Image.new('RGB', size, (40, 120, 90)).save(buf, format='HEIF')
    return buf.getvalue()


class RenderThumbnailTests(SimpleTestCase):
    def test_image_thumbnail_is_jpeg_and_fits_the_box(self):
        out = render_thumbnail(io.BytesIO(_png((600, 400))), is_pdf=False, max_px=100)
        self.assertIsNotNone(out)
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.format, 'JPEG')
        self.assertLessEqual(max(img.size), 100)
        self.assertEqual(img.size, (100, 67))  # 600x400 aspect preserved

    def test_heic_image_thumbnail(self):
        # HEIC (phone photos) needs pillow-heif; the opener is registered at
        # app startup, so render_thumbnail can decode it like any other image.
        out = render_thumbnail(io.BytesIO(_heic()), is_pdf=False, max_px=100)
        self.assertIsNotNone(out)
        self.assertEqual(Image.open(io.BytesIO(out)).format, 'JPEG')

    def test_pdf_first_page_renders_to_a_thumbnail(self):
        out = render_thumbnail(io.BytesIO(_pdf()), is_pdf=True, max_px=120)
        self.assertIsNotNone(out)
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.format, 'JPEG')
        self.assertLessEqual(max(img.size), 120)

    def test_pdf_with_a_huge_mediabox_renders_bounded(self):
        # A tiny PDF whose page is declared at 7200pt (100in) must not rasterize
        # to a giant bitmap; the scale is clamped to the target box. (resolution=1
        # dpi turns a 100px image into a 7200pt page in a ~2 KB file.)
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), (10, 10, 10)).save(buf, 'PDF', resolution=1.0)
        # Assert on the INTERMEDIATE render the clamp actually controls. The
        # final output is downscaled regardless, so it can't reveal a missing
        # clamp — a 3600x3600 intermediate would still thumbnail down to <=120.
        intermediate = _pdf_first_page(buf.getvalue(), 120)
        self.assertLessEqual(max(intermediate.size), 2 * 120)  # clamped near the box, not ~3600px
        out = render_thumbnail(io.BytesIO(buf.getvalue()), is_pdf=True, max_px=120)
        self.assertIsNotNone(out)
        self.assertLessEqual(max(Image.open(io.BytesIO(out)).size), 120)

    def test_garbage_returns_none(self):
        self.assertIsNone(render_thumbnail(io.BytesIO(b'not an image'), is_pdf=False))
        self.assertIsNone(render_thumbnail(io.BytesIO(b'not a pdf'), is_pdf=True))

    def test_empty_returns_none(self):
        self.assertIsNone(render_thumbnail(io.BytesIO(b''), is_pdf=False))
        self.assertIsNone(render_thumbnail(io.BytesIO(b''), is_pdf=True))
