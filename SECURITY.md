# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** via GitHub's
[private vulnerability reporting](../../security/advisories/new)
(Security tab → "Report a vulnerability") rather than a public issue.
You should hear back within a week.

## What counts as a vulnerability here

The public gallery is **public by design** (see the README's "Data & privacy"
section): every piece, artist, and full-resolution image is intentionally served
to anonymous visitors, and `robots.txt` only discourages indexing. Reports that
public media is reachable without authentication are expected behaviour, not
vulnerabilities.

The boundaries that *are* security-relevant:

- **Curator-only data leaking publicly** — purchase price, currency, or private
  notes appearing on (or reachable from) any public page.
- **Private documents** — `PieceDocument` files or their thumbnails being
  reachable without a staff session (they live in a separate private store and
  are only served by staff-gated views).
- **EXIF stripping** — an upload path that stores an image with its
  GPS/camera/timestamp metadata intact.
- **Authentication** — bypasses of the `/curate/`/`/admin/` staff gate or the
  django-axes login rate-limiting.
- The usual suspects: injection, XSS/CSP bypasses, CSRF, SSRF, path traversal.

## Supported versions

Only the `main` branch is supported. There are no maintained release branches;
update by pulling `main` (see the README's "Upgrading" section).
