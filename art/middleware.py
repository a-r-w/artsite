"""Custom middleware."""

import secrets


class ContentSecurityPolicyMiddleware:
    """Add a Content-Security-Policy header with a per-request script nonce.

    Defense-in-depth against XSS. Scripts and styles load only from the same
    origin ('self'); the two inline <script> blocks the site needs (the pre-paint
    theme init and the image fade-in hook) run only via a per-request nonce, so an
    injected <script> that lacks the nonce won't execute. Styles keep
    'unsafe-inline' because the detail page builds a per-piece inline style
    attribute (image dimensions + the LQIP data URI) that can't carry a nonce.
    Images allow data: (the inlined LQIP placeholder) and https: (GCS-served
    originals when STORAGE_BACKEND=gcs); everything else is locked to 'self', and
    framing/objects/base-uri are denied outright.

    Templates read the nonce as ``{{ request.csp_nonce }}``. The header is set
    with setdefault so a view can override it for a special case if ever needed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', self._policy(request.csp_nonce))
        return response

    @staticmethod
    def _policy(nonce):
        return '; '.join(
            (
                "default-src 'self'",
                f"script-src 'self' 'nonce-{nonce}'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: https:",
                "font-src 'self'",
                "connect-src 'self'",
                "object-src 'none'",
                "base-uri 'self'",
                "frame-ancestors 'none'",
                "form-action 'self'",
            )
        )
