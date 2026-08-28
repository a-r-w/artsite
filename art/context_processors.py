"""Template context shared across the public site and the curate admin."""

from .models import SiteSettings


def site_settings(request):
    """Expose the curator-editable site name and footer to every template."""
    settings = SiteSettings.load()
    return {
        'site_name': settings.site_name,
        'site_footer': settings.footer_text,
    }
