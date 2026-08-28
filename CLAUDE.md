# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django 6.0 web application for managing a personal art collection. Tracks pieces, artists, mediums, and locations behind a public gallery and a staff-only `/curate/` admin. Served dynamically (gunicorn) with pluggable media storage: the local filesystem (default) or Google Cloud Storage, selected by the `STORAGE_BACKEND` env var.

## Development Commands

```bash
# Zero-setup dev server: SQLite + local media, runs migrate then runserver
./dev.sh

# Run database migrations
python manage.py migrate

# Generate new migrations after model changes
python manage.py makemigrations

# Sample data to look at (idempotent; --clear removes only its own records)
python manage.py seed_demo

# Collect static files (done automatically in Dockerfile)
python manage.py collectstatic --no-input
```

To develop against a different DB/storage backend (e.g. Postgres + GCS), put the
env vars in a gitignored `dev.local.sh` — `dev.sh` sources it first; see
`dev.local.sh.example`.

Login rate-limiting is enforced by **django-axes** (the `/curate/login/` +
`/admin/login/` brute-force lockout). It ships DB tables, so `migrate` must run
on every target. Clear a lockout with `python manage.py axes_reset` (all) or
`axes_reset_username <username>`; a lockout also self-clears after 30 min
(`AXES_COOLOFF_TIME`), and a different network/IP is never locked.

## Testing

The suite uses SQLite + in-memory file storage (`artsite/settings_test.py`), so
it needs neither Postgres nor GCS — no network or credentials.

```bash
# Run the suite (always pass the test settings)
python manage.py test --settings=artsite.settings_test

# With coverage (dev dep: pip install -r requirements-dev.txt)
coverage run manage.py test --settings=artsite.settings_test && coverage report -m
```

Tests live in `art/tests/` (one module per concern: models, public views, curate
views, forms, security, admin, misc) with shared builders in `tests/factories.py`.
`test_security.py` holds the negative access-control tests — keep the gate matrix
there in sync when adding curate URLs. Coverage config is in `.coveragerc`.
CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, and the
suite on every push/PR.

## Browser testing (Playwright)

`scripts/playwright_smoke.py` drives the site in a real headless browser (login,
`/curate/` navigation, screenshots) against a running dev server — it's read-only
and the template for richer tests. If a new test must write, make it self-cleaning
(a throwaway `Medium` is cheapest). The topbar sign-out `<form>` precedes page
content, so scope submit-button selectors to the content (e.g.
`.curate-confirm button[type=submit]`) or you'll log yourself out.

## Environment Variables

- `DATABASE_URL` — database URL (dj-database-url format). Defaults to a throwaway SQLite file in dev; **required in production** (fails closed).
- `STORAGE_BACKEND` — `local` (default) or `gcs`; picks the media backend (fails closed on anything else)
- `MEDIA_ROOT` / `MEDIA_URL` — local filesystem media path / URL prefix (`local` backend; default `<project>/media` and `/media/`; the container sets `/data/media`)
- `PRIVATE_MEDIA_ROOT` — local filesystem path for staff-only documents, kept OUT of the public media tree (`local` backend; default `<project>/private`)
- `STATIC_ROOT` — where `collectstatic` writes (default `<project>/staticfiles`)
- `GS_BUCKET_NAME` — GCS bucket for media; **required** when `STORAGE_BACKEND=gcs` (fails closed)
- `GS_PROJECT_ID` / `GS_LOCATION` — GCS project (inferred from the key if unset) / in-bucket path prefix (default `art` prod, `art-dev` dev)
- `GOOGLE_APPLICATION_CREDENTIALS` — path to GCS service account JSON key (only needed when `STORAGE_BACKEND=gcs`)
- `ENVIRONMENT` — set to `production` to disable DEBUG and require `DJANGO_SECRET_KEY` + `DATABASE_URL` + `ALLOWED_HOSTS`; unrecognised values fail closed
- `DJANGO_SECRET_KEY` — required in production
- `ALLOWED_HOSTS` — comma-separated public hostname(s) (no scheme/port); **required in production**. `localhost`/`127.0.0.1` are always allowed (in-container healthcheck + dev), so the var only carries the public domains.
- `CSRF_TRUSTED_ORIGINS` — comma-separated trusted origins *with scheme*; defaults to `https://<host>` for each `ALLOWED_HOSTS` entry that's a domain (localhost/IPs skipped). Set explicitly only to override that.
- `SENTRY_DSN` — when set, initialises Sentry error tracking (no-op when unset)
- `LOG_FORMAT` — `console` (text) or `json`; default `json` in production, `console` in dev. Invalid values fall back to `console`.
- `LANGUAGE_CODE` / `TIME_ZONE` — locale (default `en-us` / `UTC`)

## Architecture

Single Django app (`art/`) within the `artsite/` project:

- **Models** (`art/models.py`): `Piece`, `Artist`, `Medium`, `Location`, and `PieceDocument` (staff-only files attached to a piece) use UUID primary keys; `SiteSettings` is an integer-pk singleton (pk forced to 1 in `save()`). `Piece` has a custom `save()` that auto-generates a slug from `{artist-name}-{title}`, resolving collisions by querying for a free numeric suffix (then keeping the slug stable, since it's a public URL).
- **Views** (`art/views.py`): Class-based generic views (ListView, DetailView). All querysets use `select_related()` for join optimization. `DetailView` handles NFC tag-writing workflow via `?from=tag` query param.
- **URLs** (`art/urls.py`): Standard `path()` routes — public views at the root and the staff admin under `/curate/`.
- **Admin** (`art/admin.py`): Customized admin with fieldsets, filters, search, and inline image thumbnails. Slug is read-only (auto-generated).
- **Templates**: public pages under `art/templates/art/`, the `/curate/` admin under `art/templates/curate/`. Images use lazy loading; the public detail page has a lightbox.
- **Static files**: Served via WhiteNoise middleware. Styling is hand-rolled — `normalize.css` (reset) plus `art.css` (design tokens in `:root`, with a `prefers-color-scheme` dark variant) on the public site; the admin additionally loads `curate.css` and the detail page `lightbox.css`.

### Adding a field to `Piece`

A new piece field touches several places. Work through them in order:

1. **Model + migration** (`art/models.py`): add the field, then `makemigrations`
   + `migrate`. Keep new fields nullable/`blank` and additive so existing rows
   aren't fabricated values.
2. **Form** (`art/forms.py` `PieceForm.Meta.fields`, + any widget/label) and its
   placement in `art/templates/curate/piece_form.html`.
3. **Admin** (`art/admin.py` `PieceAdmin.fieldsets`).
4. **Search**: if the field is optional (a curator can leave it unset), add a
   presence filter to `PieceListView.PRESENCE_FILTERS` (`art/views_curate.py`).
   `test_presence_filters_cover_every_optional_field` fails until you either add
   it there or list it in that test's `excluded` set. (That test only checks
   Piece's own fields — a *related* model worth filtering on, like `documents`,
   must be added by hand, using an `Exists` subquery rather than a join so a
   piece's row isn't multiplied by its related count.)
5. **Public display**: if it should appear publicly, render it in
   `art/templates/art/detail.html`. **Privacy boundary:** `notes_private`,
   `purchase_price`, and `purchase_currency` are curator-only and must never
   appear in a public template (`acquired` and `date_acquired` *are*
   intentionally public on the detail page). Give any new sensitive field an
   `assertNotContains` guard on the public detail view, alongside
   `test_private_notes_never_leak_publicly` in `test_public_views.py`.
   Staff-only **documents** (`PieceDocument`) are a separate private category:
   they live in the `STORAGES['private']` store and are reachable only through
   the staff-gated download view — never rendered on, or linked from, a public
   page (guarded in `test_documents.py` + the gate matrix in `test_security.py`).

## Storage

- **Public-media invariant**: collection media (`Piece.image`, `Artist.portrait`) in `STORAGES['default']` is intentionally **public** — served to anyone (full-resolution originals on the detail page, signed URLs on GCS / Caddy `file_server` on local), with no per-image "private" concept. The ONLY private media is `PieceDocument` (below). Guarded by `PublicMediaInvariantTests` (images served to anonymous visitors) ⟷ `test_private_notes_never_leak_publicly` / `test_documents.py` (documents never are). Because the originals are public, **EXIF is stripped on upload** (`art/images.py` `strip_image_metadata`, called from `Piece.save`/`Artist.save`): GPS/camera/timestamp metadata is removed and the EXIF orientation is baked into the pixels (so `image_width/height` match what's shown), while the ICC colour profile is preserved. Re-encodes JPEG/PNG/WEBP/TIFF/HEIF/AVIF/GIF in place (incl. MPO — which phones ship as `.jpg` — canonicalised to JPEG; a lossless WEBP stays lossless); only a freshly-uploaded (uncommitted) file is touched. **HEIC/HEIF (iPhone default) needs the metadata cleared off the decoded image explicitly** — pillow-heif's encoder re-embeds `info['exif']`/`['xmp']` otherwise, unlike the other encoders. Any animation/multi-image input (animated GIF/WEBP, multi-image HEIF / iPhone live photos) is flattened to a single still and stripped — it's a still gallery; the kept frame is the HEIF primary "key photo" (which need not be frame 0), or frame 0 for GIF/WEBP. Only unsupported formats (BMP/ICO/…) pass through untouched. Uploads are restricted to the strippable formats by `validate_image_upload` (by sniffed format, so a renamed JP2/PSD is rejected, not stored unstripped), and the stored object is named `<uuid><ext>` (`id_prefixed_filename`) so the client filename — which can encode location/date/people — can't leak in the public image/thumbnail URL. The strip fails **closed**: a decode/encode error propagates rather than storing a metadata-bearing original — `validate_image_upload` catches it first and rejects the upload with a message, so it never silently leaks. Covered per-format by `test_images.py` (the JPEG-only suite once masked a live HEIF leak).
- **Stripping existing images**: `strip_image_metadata` only touches *new* uploads, so images already in storage (pre-feature, or copied during a backend migration) keep their metadata. `manage.py backfill_strip_image_metadata` sweeps the live collection — re-strips each public image and renames it to the stem-less `<uuid><ext>` scheme, regenerating thumbnails (and refreshing `image_width/height` + the `image_lqip` placeholder for the images it rewrites). Dry-run by default; `--apply` writes; idempotent; `default_storage` only. **Run it once post-deploy** (and after any media copy) to clean the back-catalogue.
- **Blur-up placeholder (`Piece.image_lqip`)**: a tiny (~20px) base64 JPEG inlined into the detail page (`detail.html` `.piece-figure__media`'s `--lqip` background) so the reserved image box shows a blurred preview while the full-resolution original downloads; the hero then cross-fades in via the shared `[data-fade]`/`is-loaded` hook (`art/static/art/css/art.css` + `_lazy_image_loader.html`). Generated in `art/images.py` (`lqip_data_uri`/`_lqip_from_bytes`) and, like the EXIF strip, **metadata-cleaned** (pops `_METADATA_INFO_KEYS`) — it's inlined unescaped on a public page and is built from the *committed* (possibly still-unstripped) file on the legacy path, so it must not re-emit the source's EXIF/COM comment. Populated on upload (from the already-stripped bytes) and, for legacy images, by `manage.py backfill_image_lqip` (or as a side-effect of `backfill_strip_image_metadata` rewriting an image). It is deliberately **NOT generated on the request path** — the full-resolution decode it needs OOM-ed a small deployment when concurrent anonymous detail views each decoded a legacy original; `ensure_image_dimensions` only backfills the cheap header dimensions on view. Best-effort/cosmetic (`''` on any failure, template falls back to a bare `<img data-fade>`). Covered by `test_lqip.py`.
- **Backend selection**: `STORAGE_BACKEND` (in `settings.py`) picks `local` (default) or `gcs`. One resolved path (`_MEDIA_BACKEND`) drives **both** `STORAGES['default']` and easy-thumbnails' `THUMBNAIL_DEFAULT_STORAGE`, so source images and thumbnails can never split across backends. App code is storage-agnostic (uses `default_storage` / `.url` / `get_thumbnailer`), so the swap is config-only — the test suite proves it by running on `InMemoryStorage`.
- **`gcs`**: Google Cloud Storage via django-storages. Bucket from `GS_BUCKET_NAME`, in-bucket prefix `art/` (prod) or `art-dev/` (dev), overridable with `GS_LOCATION`. Thumbnails (easy-thumbnails, 400x400 sharpened) live in the same bucket. Served directly via signed URLs.
- **`local`**: Django `FileSystemStorage` under `MEDIA_ROOT`. In production nothing in Django serves `/media/` — the host Caddy `file_server` does (see `deploy/Caddyfile.artsite`). `og:image` is absolutized (the `absolute_url` template tag) since local thumbnail URLs are relative.
- **Private documents** (`PieceDocument`): staff-only files in a SEPARATE `STORAGES['private']` store the public never reaches — the `art.storage` backends refuse to emit a URL; local uses `PRIVATE_MEDIA_ROOT` (outside the Caddy-served tree), GCS a sibling `<loc>-private` prefix (never a child of the public location, so the orphan walk / media rsync can't reach it) written `default_acl=private` (owner-only — overrides the global `authenticatedRead`, so a blob isn't readable by any Google account that learns its path). Served only by the staff-gated `PieceDocumentDownloadView`. The orphan/verify tooling is scoped to `default_storage` and skips this store.
- **Document thumbnails**: each document gets a best-effort thumbnail (`art.thumbnails` — Pillow for images incl. HEIC via `pillow-heif`, `pypdfium2` for a PDF's first page; the HEIF opener is registered in `ArtConfig.ready()`), stored in the **same private store** (a thumbnail of a receipt is as sensitive as the receipt) and served only via the staff-gated `PieceDocumentThumbnailView`. Generated on upload; `manage.py backfill_document_thumbnails` fills any that are missing.
- **Orphan cleanup**: deleting a piece/artist removes its source image and thumbnails (a `post_delete` signal). To sweep files left by older deletions, `manage.py cleanup_orphan_images` lists stored files no live row references; `--delete` removes them (dry-run by default; safeguards in its docstring). Its inverse, `manage.py verify_media`, lists DB-referenced files **missing** from storage — the pre-cutover check when copying media between backends. Both run against whatever `STORAGE_BACKEND`/`ENVIRONMENT` is configured.

## Deployment

Three deployment paths (see the README + `deploy/SELF_HOSTING.md`):
1. **Docker Compose** (`docker-compose.yml`): the recommended simple path — app + Postgres + Caddy (automatic HTTPS), local storage, configured by a single `.env`; `deploy/entrypoint.sh` migrates before gunicorn.
2. **Fly.io** (`fly.toml.example` → your gitignored `fly.toml`): GCS backend. Runs migrations via `release_command`. Gunicorn with threaded workers. The GCS key is supplied as a Fly secret (`GCS_CREDENTIALS`), not baked into the image.
3. **Podman Quadlet** (`deploy/`): local-filesystem backend, rootless systemd-managed containers (app + PostgreSQL sidecar), media on a host/NAS volume served by the host Caddy. Full runbook in `deploy/SELF_HOSTING.md`; DB backups via `deploy/backup-db.sh` + the `deploy/systemd/` timer.

Worker sizing/recycling is centralised in `gunicorn.conf.py` (env-driven), shared
by all three targets.
