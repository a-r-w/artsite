"""Tiny object builders for tests — explicit, no third-party factory lib."""

from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from art.models import Artist, Location, Medium, Piece, PieceDocument

User = get_user_model()


def tiny_png(name='sample.png'):
    """A real (Pillow-valid) PNG upload, so ImageField validation passes."""
    buf = BytesIO()
    Image.new('RGB', (4, 4), (90, 140, 210)).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def tiny_pdf(name='doc.pdf'):
    """A real 1-page PDF upload (Pillow-authored), for document thumbnail tests."""
    buf = BytesIO()
    Image.new('RGB', (300, 200), (200, 90, 60)).save(buf, 'PDF')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='application/pdf')


def make_user(username='viewer', password='pw', **kw):
    """A plain authenticated, non-staff user."""
    return User.objects.create_user(username=username, password=password, **kw)


def make_staff(username='curator', password='pw', **kw):
    """A staff user — passes the curate gate (is_staff + is_active)."""
    return User.objects.create_user(username=username, password=password, is_staff=True, **kw)


def make_superuser(username='root', password='pw', **kw):
    return User.objects.create_superuser(username=username, password=password, **kw)


def make_artist(name='Frida Kahlo', **kw):
    return Artist.objects.create(name=name, **kw)


def make_medium(description='Oil'):
    # get_or_create: description is unique, so reuse within a test rather than
    # colliding when several pieces default to the same medium.
    return Medium.objects.get_or_create(description=description)[0]


def make_location(description='Studio'):
    return Location.objects.get_or_create(description=description)[0]


def make_piece(title='Self Portrait', *, artist=None, location=None, medium=None, tagged=False, **kw):
    return Piece.objects.create(
        title=title,
        artist=artist or make_artist(),
        location=location or make_location(),
        medium=medium,
        tagged=tagged,
        **kw,
    )


def make_document(piece, *, name='receipt.pdf', content=b'%PDF-1.4 fake receipt', **kw):
    """A PieceDocument with a file in the private store (for tests)."""
    upload = SimpleUploadedFile(name, content, content_type='application/pdf')
    return PieceDocument.objects.create(piece=piece, file=upload, original_name=name, size=len(content), **kw)
