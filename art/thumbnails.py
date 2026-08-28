"""Generate small thumbnails for staff documents (images and PDFs).

Best-effort and self-contained: returns JPEG bytes, or None on any failure
(unreadable file, unsupported content, render error), so a thumbnail is never a
hard requirement of a document. PDFs are rendered (first page) with pypdfium2;
images use Pillow. The result is stored in the private document store and served
only through the staff-gated thumbnail view — never via a public URL.
"""

import io
import logging

import pypdfium2 as pdfium
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_MAX_PX = 300


def render_thumbnail(fileobj, *, is_pdf, max_px=DEFAULT_MAX_PX):
    """Return JPEG thumbnail bytes for an open file, or None on any failure.

    `is_pdf` selects the path (PDF first-page render vs image decode). The image
    is fit within a max_px box (aspect preserved) and flattened to RGB so a
    transparent PNG / PDF page doesn't go black in JPEG."""
    try:
        data = fileobj.read()
        image = _pdf_first_page(data, max_px) if is_pdf else Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)  # honour a phone photo's rotation
        image = image.convert('RGB')
        image.thumbnail((max_px, max_px))
        out = io.BytesIO()
        image.save(out, 'JPEG', quality=82)
        return out.getvalue()
    except Exception:
        logger.warning('Could not render document thumbnail (is_pdf=%s)', is_pdf, exc_info=True)
        return None


def _pdf_first_page(data, max_px):
    """Render page 1 of a PDF to a PIL image via pypdfium2.

    The render scale is clamped so the output is ~max_px on the long edge
    regardless of the page's declared size: a tiny PDF with a huge MediaBox (a
    100in page is only a few hundred bytes) must NOT rasterize to a giant,
    OOM-inducing bitmap. Image.MAX_IMAGE_PIXELS guards Pillow's decoder, not
    pdfium, so the bound has to be applied here. Capped at 2x so ordinary small
    pages still render crisply before downscaling."""
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        longest_pt = max(page.get_size()) or 1
        scale = min(2.0, max_px / longest_pt)
        return page.render(scale=scale).to_pil()
    finally:
        pdf.close()
