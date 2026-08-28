"""EXIF/metadata stripping on image upload (art/images.py).

The collection is a public gallery — piece images and artist portraits are
served to anyone — so an uploaded photo's GPS/camera/timestamp metadata must be
removed before it lands in storage. Orientation is baked in (and the ICC colour
profile preserved) in the process.
"""

import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from .factories import make_artist, make_location, make_piece

# Formats re-encoded by the strip (art.images._STRIP_FORMATS). HEIF (iPhone HEIC)
# is the highest-risk GPS carrier and re-embeds metadata unless explicitly
# cleared, so it MUST be in the matrix.
STRIP_FORMATS = (('JPEG', 'jpg'), ('PNG', 'png'), ('WEBP', 'webp'), ('TIFF', 'tiff'), ('HEIF', 'heic'))

XMP_WITH_GPS = (
    b'<?xpacket begin="\xef\xbb\xbf"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" '
    b'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
    b'exif:GPSLatitude="51,30.0N" photoshop:Credit="SENTINEL-XMP-CREDIT"/>'
    b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def _exif(orientation=None, make='SENTINEL-CAMERA-MAKE'):
    e = Image.Exif()
    if orientation:
        e[0x0112] = orientation  # Orientation
    e[0x010F] = make  # Make
    e[0x0110] = 'SENTINEL-CAMERA-MODEL'  # Model
    e[0x0132] = '2024:01:02 03:04:05'  # DateTime
    return e


def exif_jpeg(name='photo.jpg', size=(40, 20), orientation=6, icc=False, xmp=None, comment=None):
    """A JPEG carrying EXIF (orientation + camera + timestamp), optionally an ICC
    profile, XMP packet, and/or COM comment — the kind of file a camera produces."""
    img = Image.new('RGB', size, (123, 45, 67))
    kwargs = {'exif': _exif(orientation)}
    if icc:
        from PIL import ImageCms

        kwargs['icc_profile'] = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    if xmp:
        kwargs['xmp'] = xmp
    if comment:
        kwargs['comment'] = comment
    buf = io.BytesIO()
    img.save(buf, format='JPEG', **kwargs)
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


def image_with_exif(fmt, ext, size=(40, 20)):
    """A single-frame image of `fmt` carrying EXIF (camera make sentinel)."""
    img = Image.new('RGB', size, (90, 110, 130))
    buf = io.BytesIO()
    img.save(buf, format=fmt, exif=_exif().tobytes())
    return SimpleUploadedFile(f'photo.{ext}', buf.getvalue(), content_type=f'image/{ext}')


def multiframe_heif(name='live.heic', primary_index=0):
    """A multi-image HEIF (≈ an iPhone live photo / burst) carrying EXIF. The two
    frames have DISTINCT sizes so a test can tell which one was kept; `primary_index`
    designates the HEIF primary item (the "key photo")."""
    frames = [
        Image.new('RGB', (40, 20), (200, 50, 50)),  # index 0
        Image.new('RGB', (10, 30), (50, 50, 200)),
    ]  # index 1
    buf = io.BytesIO()
    frames[0].save(
        buf, format='HEIF', save_all=True, append_images=frames[1:], exif=_exif().tobytes(), primary_index=primary_index
    )
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/heic')


class ExifStripTests(TestCase):
    def _stored(self, fieldfile):
        fieldfile.open()
        try:
            return fieldfile.read()
        finally:
            fieldfile.close()

    def _piece(self):
        return make_piece(artist=make_artist(), location=make_location())

    def test_source_fixture_actually_has_exif(self):
        # Guard the test itself: the fixture must carry EXIF, or the strip tests
        # would pass vacuously.
        src = Image.open(io.BytesIO(exif_jpeg().read()))
        self.assertTrue(src.getexif())

    def test_exif_is_stripped_for_every_strip_format(self):
        # The JPEG-only suite once masked a live HEIF leak; exercise every format
        # the strip claims to handle, asserting the camera sentinel is gone from
        # both the parsed EXIF and the raw bytes.
        for fmt, ext in STRIP_FORMATS:
            with self.subTest(fmt=fmt):
                upload = image_with_exif(fmt, ext)
                self.assertIn(b'SENTINEL-CAMERA-MAKE', upload.read(), f'{fmt} fixture lacks EXIF')
                upload.seek(0)
                piece = self._piece()
                piece.image = upload
                piece.save()
                stored = self._stored(piece.image)
                self.assertNotIn(b'SENTINEL-CAMERA-MAKE', stored, f'{fmt} leaked EXIF make bytes')
                self.assertNotIn(271, list(Image.open(io.BytesIO(stored)).getexif().keys()), f'{fmt} kept the Make tag')

    def test_heif_exif_and_xmp_gps_are_stripped(self):
        # HEIC is the iPhone default and re-embeds EXIF+XMP on save; pin it hard.
        img = Image.new('RGB', (30, 20), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format='HEIF', exif=_exif(orientation=1).tobytes(), xmp=XMP_WITH_GPS)
        upload = SimpleUploadedFile('iphone.heic', buf.getvalue(), content_type='image/heic')
        raw_src = upload.read()
        upload.seek(0)
        self.assertIn(b'SENTINEL-CAMERA-MAKE', raw_src)  # fixture guards (non-vacuous)
        self.assertIn(b'SENTINEL-XMP-CREDIT', raw_src)
        piece = self._piece()
        piece.image = upload
        piece.save()
        stored = self._stored(piece.image)
        self.assertEqual(list(Image.open(io.BytesIO(stored)).getexif().keys()), [])
        for sentinel in (b'SENTINEL-CAMERA-MAKE', b'SENTINEL-XMP-CREDIT', b'GPSLatitude'):
            self.assertNotIn(sentinel, stored)

    def test_orientation_is_baked_into_stored_pixels_and_dimensions(self):
        # Orientation=6 rotates a 40x20 source to 20x40 when displayed; baking it
        # in makes the stored pixels (and the captured box) match.
        piece = self._piece()
        piece.image = exif_jpeg(size=(40, 20), orientation=6)
        piece.save()
        self.assertEqual((piece.image_width, piece.image_height), (20, 40))
        self.assertEqual(Image.open(io.BytesIO(self._stored(piece.image))).size, (20, 40))

    def test_xmp_metadata_is_stripped_too(self):
        # XMP can also carry GPS/creator; re-encoding must drop it, not just EXIF.
        upload = exif_jpeg(xmp=XMP_WITH_GPS)
        self.assertIn(b'SENTINEL-XMP-CREDIT', upload.read())  # guard: fixture has it
        upload.seek(0)
        piece = self._piece()
        piece.image = upload
        piece.save()
        stored = self._stored(piece.image)
        self.assertNotIn(b'SENTINEL-XMP-CREDIT', stored)
        self.assertNotIn(b'GPSLatitude', stored)

    def test_icc_colour_profile_is_preserved(self):
        try:
            upload = exif_jpeg(icc=True)
        except ImportError:
            self.skipTest('PIL.ImageCms not available')
        self.assertTrue(  # guard: fixture actually carries an ICC profile
            Image.open(io.BytesIO(upload.read())).info.get('icc_profile'),
            'fixture regression: source JPEG no longer carries an ICC profile',
        )
        upload.seek(0)
        piece = self._piece()
        piece.image = upload
        piece.save()
        self.assertTrue(Image.open(io.BytesIO(self._stored(piece.image))).info.get('icc_profile'))

    def test_artist_portrait_exif_is_stripped(self):
        artist = make_artist()
        artist.portrait = exif_jpeg(name='portrait.jpg')
        artist.save()
        self.assertEqual(list(Image.open(io.BytesIO(self._stored(artist.portrait))).getexif().keys()), [])

    def test_mpo_phone_photo_is_stripped(self):
        # Phones emit MPO (HDR/portrait/burst) with a .jpg name; it reports
        # format='MPO' and must be canonicalised to JPEG and stripped, not passed
        # through.
        a = Image.new('RGB', (40, 20), (200, 0, 0))
        b = Image.new('RGB', (40, 20), (0, 0, 200))
        buf = io.BytesIO()
        a.save(buf, format='MPO', save_all=True, append_images=[b], exif=_exif().tobytes())
        raw = buf.getvalue()
        self.assertEqual(Image.open(io.BytesIO(raw)).format, 'MPO')  # fixture really is MPO
        self.assertIn(b'SENTINEL-CAMERA-MAKE', raw)
        piece = self._piece()
        piece.image = SimpleUploadedFile('photo.jpg', raw, content_type='image/jpeg')
        piece.save()
        stored = self._stored(piece.image)
        img = Image.open(io.BytesIO(stored))
        self.assertEqual(list(img.getexif().keys()), [])
        self.assertEqual(getattr(img, 'n_frames', 1), 1)  # flattened to the key frame
        self.assertNotIn(b'SENTINEL-CAMERA-MAKE', stored)

    def test_avif_is_stripped(self):
        from PIL import features

        if not features.check('avif'):
            self.skipTest('AVIF not supported in this build')
        buf = io.BytesIO()
        Image.new('RGB', (40, 20), (0, 200, 0)).save(buf, format='AVIF', exif=_exif().tobytes())
        piece = self._piece()
        piece.image = SimpleUploadedFile('photo.avif', buf.getvalue(), content_type='image/avif')
        piece.save()
        stored = self._stored(piece.image)
        self.assertEqual(list(Image.open(io.BytesIO(stored)).getexif().keys()), [])
        self.assertNotIn(b'SENTINEL-CAMERA-MAKE', stored)

    def test_png_text_chunks_are_stripped(self):
        # PNG identifying metadata lives in tEXt/iTXt chunks (Author/Comment/XMP
        # from screenshots, GIMP, Photoshop), NOT EXIF.
        from PIL.PngImagePlugin import PngInfo

        meta = PngInfo()
        meta.add_text('Author', 'SENTINEL-PNG-AUTHOR')
        meta.add_itxt('Description', 'SENTINEL-PNG-ITXT')
        buf = io.BytesIO()
        Image.new('RGB', (40, 20), (90, 110, 130)).save(buf, format='PNG', pnginfo=meta)
        raw = buf.getvalue()
        self.assertIn(b'SENTINEL-PNG-AUTHOR', raw)  # fixture guards (non-vacuous)
        self.assertIn(b'SENTINEL-PNG-ITXT', raw)
        piece = self._piece()
        piece.image = SimpleUploadedFile('text.png', raw, content_type='image/png')
        piece.save()
        stored = self._stored(piece.image)
        self.assertNotIn(b'SENTINEL-PNG-AUTHOR', stored)
        self.assertNotIn(b'SENTINEL-PNG-ITXT', stored)

    def test_jpeg_com_comment_is_stripped(self):
        upload = exif_jpeg(comment=b'SENTINEL-JPEG-COM')
        self.assertIn(b'SENTINEL-JPEG-COM', upload.read())  # fixture guard
        upload.seek(0)
        piece = self._piece()
        piece.image = upload
        piece.save()
        self.assertNotIn(b'SENTINEL-JPEG-COM', self._stored(piece.image))

    def test_lossless_webp_is_preserved_not_degraded(self):
        # A lossless WEBP (sharp graphics) must stay lossless on re-encode, while
        # still being stripped. A lossy re-encode would change the checkerboard.
        img = Image.new('RGB', (32, 32))
        img.putdata([(255, 255, 255) if (x + y) % 2 else (0, 0, 0) for y in range(32) for x in range(32)])
        buf = io.BytesIO()
        img.save(buf, format='WEBP', lossless=True, exif=_exif().tobytes())
        piece = self._piece()
        piece.image = SimpleUploadedFile('art.webp', buf.getvalue(), content_type='image/webp')
        piece.save()
        stored = self._stored(piece.image)
        out = Image.open(io.BytesIO(stored)).convert('RGB')
        self.assertEqual(list(out.getdata()), list(img.getdata()))  # lossless preserved
        self.assertEqual(list(Image.open(io.BytesIO(stored)).getexif().keys()), [])  # and stripped

    def test_alpha_and_transparency_survive_the_strip(self):
        # Re-encoding must not flatten transparency.
        for fmt, ext, ct in (('PNG', 'png', 'image/png'), ('WEBP', 'webp', 'image/webp')):
            with self.subTest(fmt=fmt):
                img = Image.new('RGBA', (40, 20), (200, 50, 50, 128))
                img.putpixel((0, 0), (10, 20, 30, 0))  # fully transparent corner
                buf = io.BytesIO()
                img.save(buf, format=fmt)
                piece = self._piece()
                piece.image = SimpleUploadedFile(f'alpha.{ext}', buf.getvalue(), content_type=ct)
                piece.save()
                out = Image.open(io.BytesIO(self._stored(piece.image))).convert('RGBA')
                self.assertEqual(out.getpixel((0, 0))[3], 0)  # corner stayed transparent
                self.assertEqual(out.getpixel((20, 10))[3], 128)  # body kept semi-transparency
        # Palette GIF with a transparency index keeps it.
        base = Image.new('RGBA', (20, 20), (255, 0, 0, 255))
        base.putpixel((0, 0), (0, 0, 0, 0))
        buf = io.BytesIO()
        base.convert('P').save(buf, format='GIF', transparency=0)
        piece = self._piece()
        piece.image = SimpleUploadedFile('transp.gif', buf.getvalue(), content_type='image/gif')
        piece.save()
        self.assertIn('transparency', Image.open(io.BytesIO(self._stored(piece.image))).info)

    def test_committed_image_is_not_re_encoded_on_a_later_save(self):
        piece = self._piece()
        piece.image = exif_jpeg()
        piece.save()
        before = self._stored(piece.image)
        # A later edit with no new upload must not even decode the stored image
        # (the strip is a no-op for an already-committed file) — and the stored
        # bytes stay identical.
        piece.refresh_from_db()
        piece.title = 'Renamed, no new image'
        with mock.patch('art.images.Image.open') as opened:
            piece.save()
        opened.assert_not_called()
        self.assertEqual(before, self._stored(piece.image))

    def test_multiframe_heif_live_photo_is_flattened_and_stripped(self):
        # A multi-image HEIC (iPhone live photo) must be flattened to the still
        # AND stripped — not skipped (which would leave GPS on a normal-looking
        # iPhone upload).
        upload = multiframe_heif()
        raw = upload.read()
        upload.seek(0)
        self.assertGreater(Image.open(io.BytesIO(raw)).n_frames, 1)  # fixture is multi-frame
        self.assertIn(b'SENTINEL-CAMERA-MAKE', raw)
        piece = self._piece()
        piece.image = upload
        piece.save()
        stored = Image.open(io.BytesIO(self._stored(piece.image)))
        self.assertEqual(getattr(stored, 'n_frames', 1), 1)  # flattened to the still
        self.assertEqual(list(stored.getexif().keys()), [])  # and stripped

    def test_multiframe_heif_keeps_the_primary_key_photo(self):
        # The HEIF primary item (the "key photo") need NOT be the first stored
        # frame; the kept still must be it, not blindly frame 0.
        upload = multiframe_heif(primary_index=1)  # key photo is the 10x30 frame
        piece = self._piece()
        piece.image = upload
        piece.save()
        stored = Image.open(io.BytesIO(self._stored(piece.image)))
        self.assertEqual(stored.size, (10, 30))  # the primary, NOT frame 0 (40x20)
        self.assertEqual(getattr(stored, 'n_frames', 1), 1)  # flattened
        self.assertEqual(list(stored.getexif().keys()), [])  # and stripped

    def test_animated_webp_is_flattened(self):
        # Still gallery: an animated WEBP is flattened to the first frame, not
        # preserved as animation. (Single-frame WEBP EXIF stripping is covered by
        # test_exif_is_stripped_for_every_strip_format.)
        frames = [Image.new('RGB', (20, 20), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        buf = io.BytesIO()
        frames[0].save(buf, format='WEBP', save_all=True, append_images=frames[1:], duration=100, loop=0)
        upload = SimpleUploadedFile('anim.webp', buf.getvalue(), content_type='image/webp')
        self.assertGreater(Image.open(io.BytesIO(upload.read())).n_frames, 1)  # fixture guard
        upload.seek(0)
        piece = self._piece()
        piece.image = upload
        piece.save()
        self.assertEqual(getattr(Image.open(io.BytesIO(self._stored(piece.image))), 'n_frames', 1), 1)

    def test_animated_gif_is_flattened_and_comment_stripped(self):
        # GIF carries no EXIF/GPS, but can hold a comment block; flatten to one
        # frame and drop the comment.
        frames = [Image.new('RGB', (20, 20), c).convert('P') for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        buf = io.BytesIO()
        frames[0].save(
            buf,
            format='GIF',
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
            comment=b'SENTINEL-GIF-COMMENT',
        )
        raw = buf.getvalue()
        self.assertGreater(Image.open(io.BytesIO(raw)).n_frames, 1)  # fixture guard
        self.assertIn(b'SENTINEL-GIF-COMMENT', raw)
        piece = self._piece()
        piece.image = SimpleUploadedFile('anim.gif', raw, content_type='image/gif')
        piece.save()
        stored = self._stored(piece.image)
        self.assertEqual(getattr(Image.open(io.BytesIO(stored)), 'n_frames', 1), 1)  # flattened
        self.assertNotIn(b'SENTINEL-GIF-COMMENT', stored)  # comment dropped

    def test_unsupported_format_passes_through_untouched(self):
        # A format outside _STRIP_FORMATS (BMP) is stored byte-identical — the
        # strip never mangles something it doesn't re-encode.
        buf = io.BytesIO()
        Image.new('RGB', (20, 20), (10, 20, 30)).save(buf, format='BMP')
        raw = buf.getvalue()
        piece = self._piece()
        piece.image = SimpleUploadedFile('plain.bmp', raw, content_type='image/bmp')
        piece.save()
        self.assertEqual(self._stored(piece.image), raw)

    def test_public_thumbnail_is_metadata_clean_even_from_a_dirty_original(self):
        # The public 400x400 thumbnail (og:image, list/portrait thumbs) must never
        # carry source metadata. Pin easy-thumbnails' clean-thumbnail behaviour
        # using a DIRTY, unstripped source so it also guards the legacy / fail-open
        # case where the original the thumbnailer reads still has EXIF/XMP.
        from easy_thumbnails.files import get_thumbnailer

        buf = io.BytesIO()
        Image.new('RGB', (800, 600), (10, 20, 30)).save(buf, format='JPEG', exif=_exif().tobytes(), xmp=XMP_WITH_GPS)
        raw = buf.getvalue()
        self.assertIn(b'SENTINEL-CAMERA-MAKE', raw)  # dirty-source guard (non-vacuous)
        piece = self._piece()
        # Commit the raw bytes directly (save=False bypasses Piece.save's strip).
        piece.image.save('legacy.jpg', SimpleUploadedFile('legacy.jpg', raw), save=False)
        type(piece).objects.filter(pk=piece.pk).update(image=piece.image.name)
        piece.refresh_from_db()
        thumb = get_thumbnailer(piece.image)['thumb']
        thumb.open()
        try:
            tb = thumb.read()
        finally:
            thumb.close()
        self.assertEqual(list(Image.open(io.BytesIO(tb)).getexif().keys()), [])
        for sentinel in (b'SENTINEL-CAMERA-MAKE', b'SENTINEL-XMP-CREDIT', b'GPSLatitude'):
            self.assertNotIn(sentinel, tb)

    def test_unsupported_metadata_capable_format_fails_closed_at_save(self):
        # JP2 (carries EXIF/XMP, can't be stripped here) reaching the model layer
        # directly — e.g. the Django admin, which skips the form allowlist — is
        # rejected at save, not stored unstripped.
        buf = io.BytesIO()
        try:
            Image.new('RGB', (8, 8), (1, 2, 3)).save(buf, format='JPEG2000')
        except Exception:
            self.skipTest('JPEG2000 encoder unavailable')
        piece = self._piece()
        piece.image = SimpleUploadedFile('x.jp2', buf.getvalue(), content_type='image/jp2')
        with self.assertRaises(ValueError):
            piece.save()

    def test_strip_failure_fails_closed_at_model_save(self):
        # Fail closed: if the re-encode raises, save() propagates rather than
        # storing a metadata-bearing original (no leaky file is written).
        piece = self._piece()
        piece.image = exif_jpeg()
        with mock.patch('art.images.ImageOps.exif_transpose', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                piece.save()

    def test_strip_failure_is_rejected_by_the_form(self):
        # The curate form turns the same failure into a clean validation error.
        from art.forms import PieceForm

        with mock.patch('art.images.ImageOps.exif_transpose', side_effect=RuntimeError('boom')):
            form = PieceForm(
                data={'title': 'X', 'artist': str(make_artist().id), 'location': str(make_location().id)},
                files={'image': exif_jpeg()},
            )
            self.assertFalse(form.is_valid())
            self.assertIn('image', form.errors)
