"""Storage backends for staff-only private files (a piece's documents).

These refuse to produce a public URL: ``url()`` raises, so any accidental
attempt to expose a private file in a template or response fails loudly in
development instead of leaking it. That guardrail matters most on GCS, whose
normal ``url()`` would mint a signed, shareable URL. Private files are reached
only through the staff-gated download view, which streams them via
``storage.open()`` — never ``.url``.
"""

from django.core.files.storage import FileSystemStorage, InMemoryStorage
from storages.backends.gcloud import GoogleCloudStorage

_NO_URL = 'Private documents have no public URL — serve them via the staff-gated download view.'


class _NoPublicURL:
    """Mixin: a storage whose files are never addressable by URL."""

    def url(self, name, *args, **kwargs):
        raise NotImplementedError(_NO_URL)


class PrivateFileSystemStorage(_NoPublicURL, FileSystemStorage):
    """Local private store (self-hosted backend); lives outside the media tree."""


class PrivateGoogleCloudStorage(_NoPublicURL, GoogleCloudStorage):
    """Private in-bucket prefix (GCS backend); never signed out."""


class PrivateInMemoryStorage(_NoPublicURL, InMemoryStorage):
    """In-memory private store for the test suite."""
