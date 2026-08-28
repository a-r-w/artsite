"""Image processing for uploads.

EXIF/metadata stripping for the publicly-served originals (piece images, artist
portraits). The collection is a public gallery — originals are reachable by
anyone — so the files must not carry a photo's GPS coordinates, camera serial,
or capture timestamps. See the "public media" invariant in CLAUDE.md.

`strip_metadata_bytes` is the bytes-level core, shared by the upload path
(`strip_image_metadata`, below) and the `backfill_strip_image_metadata`
management command that cleans images already in storage.
"""

import base64
import io

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# Raster formats re-encoded to a single, metadata-stripped frame. Animated GIF/
# WEBP and multi-image HEIF/AVIF (iPhone live photos, HDR/portrait MPO) are
# flattened to one frame — this is a still-image gallery, not a video one. Truly
# unsupported formats (BMP, ICO, …) pass through untouched.
_STRIP_FORMATS = frozenset({'JPEG', 'PNG', 'WEBP', 'TIFF', 'HEIF', 'AVIF', 'GIF'})

# Pillow reports some EXIF-carrying JPEG-family containers under their own format
# name; map them to the canonical encoder so they take the strip path and re-save
# as plain JPEG. MPO is the dangerous one — phones emit it for HDR/portrait/burst
# and ship it with a .jpg name, so without this it slips through as pass-through
# and leaks GPS/camera metadata.
_FORMAT_ALIASES = {'MPO': 'JPEG', 'JFIF': 'JPEG'}

# Formats we don't re-encode but accept anyway (stored unchanged) because they
# carry no EXIF/GPS. Anything else Pillow can open but we can't strip (JP2/PSD/…)
# is rejected rather than stored — fail closed, since it could carry metadata.
_PASS_THROUGH_FORMATS = frozenset({'BMP', 'DIB', 'ICO', 'ICNS'})

# Image formats accepted on upload: exactly those we re-encode/strip (plus the
# JPEG-family aliases). Anything else Pillow can open (JPEG2000/PSD/BMP/…) is
# rejected at the form so an un-strippable EXIF/XMP carrier can never be stored
# and served publicly. Imported by forms.validate_image_upload.
ACCEPTED_UPLOAD_FORMATS = _STRIP_FORMATS | set(_FORMAT_ALIASES)

# Metadata containers cleared off the decoded image before re-encoding. Most
# Pillow encoders embed these only when passed explicitly, BUT some re-emit them
# from img.info on save with no save kwargs at all: pillow-heif re-emits
# exif/xmp (HEIF/AVIF), and the JPEG/GIF encoders re-emit comment. So popping
# these is what actually strips that metadata for those formats — not optional.
# (PNG text chunks are dropped separately: copy()/transpose() sheds .text and we
# never pass pnginfo=.) ICC is captured + re-passed, so colour is unaffected.
_METADATA_INFO_KEYS = ('exif', 'xmp', 'XML:com.adobe.xmp', 'metadata', 'comment')

# Keep originals full-resolution and high quality — only metadata is removed.
# (The upload size cap in forms.py bounds the INPUT; q95/subsampling=0 can inflate
# a near-cap JPEG ~2x, with MAX_IMAGE_PIXELS the only hard ceiling on output size.)
_SAVE_KWARGS = {
    'JPEG': {'quality': 95, 'subsampling': 0},
    'WEBP': {'quality': 95, 'method': 6},
    'HEIF': {'quality': 95},
    'AVIF': {'quality': 95},
}


def _webp_is_lossless(data):
    """Whether a WEBP byte string is lossless (carries a VP8L chunk). Pillow
    doesn't expose a source's lossless flag, so sniff the RIFF container to avoid
    silently degrading a lossless WEBP (screenshot / pixel-art) to lossy on
    re-encode. Walks chunks so the VP8X extended (alpha) container is handled."""
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        return False
    pos, n = 12, len(data)
    while pos + 8 <= n:
        chunk = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], 'little')
        if chunk == b'VP8L':
            return True
        if chunk == b'VP8 ':
            return False
        pos += 8 + size + (size & 1)  # chunks are padded to an even length
    return False


def image_has_metadata(data):
    """Whether the image bytes carry metadata the strip would remove (EXIF incl.
    orientation, XMP, comment, PNG text chunks). Lets the backfill skip files
    that are already clean instead of needlessly re-encoding the whole library."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if any(k in img.info for k in _METADATA_INFO_KEYS):
                return True
            if len(img.getexif()):
                return True
            if getattr(img, 'text', None):
                return True
    except Exception:
        return False
    return False


def strip_metadata_bytes(data):
    """Return `data` re-encoded with EXIF/XMP/IPTC removed, or None if the format
    is a vetted metadata-free pass-through (BMP/ICO) that should be stored
    unchanged.

    Bakes in EXIF orientation, preserves the ICC colour profile, flattens any
    animation/multi-image input to a single still (keeping the HEIF primary "key
    photo", or frame 0 for GIF/WEBP), and keeps a lossless WEBP lossless.

    Raises on a decode/encode error OR on a format we can't strip but don't trust
    as metadata-free (JP2/PSD/…) — the caller fails closed (the upload form
    rejects it; other paths abort) rather than store a possibly-metadata-bearing
    original.
    """
    with Image.open(io.BytesIO(data)) as img:
        fmt = _FORMAT_ALIASES.get(img.format, img.format)
        if fmt not in _STRIP_FORMATS:
            if fmt in _PASS_THROUGH_FORMATS:
                return None  # metadata-free: store unchanged
            raise ValueError(f'cannot strip metadata from unsupported image format: {fmt}')
        icc = img.info.get('icc_profile')
        # Re-encoding the open image to a single frame flattens any animation /
        # multi-image input. Deliberately do NOT seek to frame 0: Image.open
        # already lands on the HEIF primary item — a HEIC's "key photo", which
        # need not be the first stored frame (iPhone live photos / bursts) — and
        # on frame 0 for GIF/WEBP. Operate on that as-opened frame.
        cleaned = ImageOps.exif_transpose(img)  # apply + drop orientation
        for key in _METADATA_INFO_KEYS:
            cleaned.info.pop(key, None)  # see _METADATA_INFO_KEYS note
        save_kwargs = dict(_SAVE_KWARGS.get(fmt, {}))
        if icc:
            save_kwargs['icc_profile'] = icc
        if fmt == 'WEBP' and _webp_is_lossless(data):
            save_kwargs['lossless'] = True  # don't degrade a lossless source
        buf = io.BytesIO()
        cleaned.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue()


def strip_image_metadata(fieldfile):
    """Re-encode a freshly-uploaded image in place, dropping EXIF/XMP/IPTC.

    Operates on a model `FieldFile`: only a just-assigned upload (an uncommitted
    file) is touched — a committed file already in storage is left alone, so a
    normal edit never re-encodes it. (Images already in storage are cleaned by the
    `backfill_strip_image_metadata` command, not here.)

    Fails CLOSED: a decode/encode error PROPAGATES rather than storing a
    metadata-bearing original. The curate form catches this first
    (`validate_image_upload`) and rejects the upload with a message; on any other
    path (admin, ORM) the save fails instead of leaking. A pass-through format
    with no metadata to strip (BMP/ICO/…) is stored unchanged.
    """
    if not fieldfile or getattr(fieldfile, '_committed', True):
        return  # no file, or an existing stored file — don't touch it

    source = fieldfile.file
    source.seek(0)
    out = strip_metadata_bytes(source.read())  # raises on a decode/encode error
    if out is None:  # pass-through format (BMP/ICO/…): no metadata, store as-is
        source.seek(0)
        return

    # Replace the uncommitted content (keep the name); the model save writes this
    # stripped version and reads the corrected dimensions from it.
    fieldfile.file = ContentFile(out, name=fieldfile.name)
    fieldfile.__dict__.pop('_dimensions_cache', None)  # force re-read of stripped dims


# Long edge of the inlined blur-up placeholder. Tiny on purpose: a ~20px JPEG is
# only a few hundred bytes of base64 — small enough to inline in the detail HTML
# — and the detail-page CSS upscales + blurs it to fill the reserved image box
# while the full-resolution original downloads. See Piece.image_lqip.
_LQIP_MAX_EDGE = 20


def _lqip_from_bytes(data):
    """Tiny blurred-placeholder JPEG data URI for image `data`. Raises on a
    decode/encode error — callers that must not fail (the save/view paths) go
    through `lqip_data_uri`, which swallows it."""
    with Image.open(io.BytesIO(data)) as img:
        # Decode at reduced scale where the format allows it (JPEG DCT scaling):
        # the placeholder is ~20px, so a multi-megapixel original must NOT be
        # decoded to full-size pixels in memory. Keeps the generation cheap
        # enough to run on a small box (it once OOM-ed decoding full-res files).
        img.draft('RGB', (_LQIP_MAX_EDGE * 4, _LQIP_MAX_EDGE * 4))
        # exif_transpose so a legacy (un-stripped) original is oriented like the
        # displayed pixels; a no-op on an already-stripped upload. RGB so any
        # palette/alpha/CMYK source survives the JPEG encode.
        tiny = ImageOps.exif_transpose(img).convert('RGB')
        tiny.thumbnail((_LQIP_MAX_EDGE, _LQIP_MAX_EDGE))  # preserves aspect ratio
        # Drop metadata the JPEG encoder re-emits from img.info — notably the COM
        # comment (a geotag/owner carrier). Mirrors strip_metadata_bytes: a LEGACY
        # image's LQIP is built from the still-UNstripped committed file (by the
        # backfill commands), so it must clean it too, not rely on the upload-time
        # strip. (exif_transpose already shed the EXIF.)
        for key in _METADATA_INFO_KEYS:
            tiny.info.pop(key, None)
        buf = io.BytesIO()
        tiny.save(buf, format='JPEG', quality=40)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/jpeg;base64,{b64}'


def lqip_data_uri(fieldfile):
    """Best-effort blur-up placeholder data URI for a stored/uploaded image, or
    '' if one can't be produced. Unlike the metadata strip this NEVER raises — a
    placeholder is purely cosmetic and must not block a save or a page view.
    Reads the file's bytes (an uncommitted upload or a committed stored object)."""
    if not fieldfile:
        return ''
    try:
        # Read via the open file handle WITHOUT closing it — at upload time this
        # is the uncommitted, stripped ContentFile that the model save still has
        # to write to storage (mirrors strip_image_metadata's read pattern).
        f = fieldfile.file
        f.seek(0)
        data = f.read()
        f.seek(0)
        return _lqip_from_bytes(data)
    except Exception:
        return ''
