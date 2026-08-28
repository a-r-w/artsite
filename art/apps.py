from django.apps import AppConfig


class ArtConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'art'

    def ready(self):
        # Make Pillow's decompression-bomb threshold explicit rather than relying
        # on the library default: an image above this pixel count raises
        # DecompressionBombError instead of being decoded into memory. Pairs with
        # the upload byte cap in forms (a small file can still claim huge pixels).
        import pillow_heif
        from django.conf import settings
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = getattr(settings, 'MAX_IMAGE_PIXELS', None) or Image.MAX_IMAGE_PIXELS

        # Teach Pillow to read HEIC/HEIF so phone-camera uploads (and their
        # document thumbnails) work — Pillow has no native HEIF support.
        pillow_heif.register_heif_opener()
