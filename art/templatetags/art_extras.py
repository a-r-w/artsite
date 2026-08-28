"""Project-specific template helpers."""

import os

from django import template

register = template.Library()


@register.filter
def file_extension(name):
    """Uppercase file extension without the dot (e.g. 'receipt.pdf' -> 'PDF'),
    for the document-list fallback tile. Falls back to 'FILE' when there's none."""
    ext = os.path.splitext(name or '')[1].lstrip('.').upper()
    return ext or 'FILE'


@register.simple_tag(takes_context=True)
def absolute_url(context, url):
    """Turn a (possibly relative) URL into an absolute one using the current
    request — needed for tags consumed off-site such as og:image.

    Image/thumbnail URLs are already absolute under the GCS storage backend but
    relative ('/media/...') under the local filesystem backend.
    ``build_absolute_uri`` leaves an already-absolute URL unchanged and prefixes
    scheme + host onto a relative one. An empty value (e.g. a thumbnail that
    failed to generate, which ``thumbnail_url`` returns as '') stays empty
    rather than becoming a link to the current page.
    """
    if not url:
        return ''
    request = context.get('request')
    return request.build_absolute_uri(url) if request else url
