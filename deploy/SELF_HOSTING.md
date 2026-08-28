# Self-hosting artsite on Podman with local storage

This guide runs artsite on a home Linux server with **rootless Podman Quadlet**,
storing media on a **local (NAS-mounted) filesystem** instead of Google Cloud
Storage. It's the advanced, no-Docker-daemon path; if you just want the simplest
deploy, use **[Docker Compose](../README.md#docker-compose-recommended)** instead
(app + Postgres + Caddy, automatic HTTPS, one `.env`).

The bulk of the guide is a **fresh install** (§1–§7), written against a Debian
box with a dedicated service user (§2) — on another distro, skip §2 and read
`artsite` as your own user. Already running on Fly.io + GCS and want to move
that collection onto your own hardware? §8 is the migration appendix; §9 covers
staying on Fly/GCS.

The app supports two storage backends, chosen at runtime by `STORAGE_BACKEND`
(see `artsite/settings.py`):

| Deployment            | `STORAGE_BACKEND` | Media stored in             | Media served by         |
| --------------------- | ----------------- | --------------------------- | ----------------------- |
| Fly.io (`fly.toml.example`) | `gcs`       | your GCS bucket             | GCS signed URLs         |
| Self-host (this guide)| `local`           | `MEDIA_ROOT` (`/data/media`)| host Caddy `file_server`|

Both are first-class — nothing here removes the GCS option.

---

## 1. What you need

- A Linux server with **rootless Podman** (Quadlet support: Podman 4.4+).
- **Caddy** running on the host; this guide adds a `/media/` handler to it.
- A **NAS path** for media, bind-mounted into the app container.
- **Local SSD** for the Postgres data volume — **never put Postgres on NFS/NAS**
  (its locking/fsync assumptions corrupt data over NFS). The NAS is for media and
  for *backups* of the DB, not the live data directory.
- Inbound **80/443** port-forwarded to the server; Caddy provisions HTTPS
  automatically for your domain.

## 2. Debian: install Podman, create a dedicated user

Written against **Debian 13 “trixie”**. Debian 12 “bookworm” ships Podman 4.3,
which predates Quadlet — use trixie, a newer podman from backports, or fall back
to [Docker Compose](../README.md#docker-compose-recommended).

```bash
sudo apt update
sudo apt install podman passt uidmap slirp4netns git systemd-container
```

(`passt`/`slirp4netns` provide rootless networking; `systemd-container` gives
you `machinectl` for clean logins as the service user.)

Run the app under a **dedicated, unprivileged user**, so the site — and anything
that compromises it — stays isolated from the rest of the box:

```bash
sudo useradd --create-home --shell /bin/bash artsite   # created with no password: locked, reached via sudo
sudo loginctl enable-linger artsite    # user services start at boot and survive logout

# Rootless Podman needs a subordinate UID/GID range. Debian's useradd allocates
# one automatically — verify, and only add one manually if the grep is empty:
grep artsite /etc/subuid /etc/subgid ||
  sudo usermod --add-subuids 200000-265535 --add-subgids 200000-265535 artsite
```

To work as that user, open a real login session — `systemctl --user` needs the
session's environment to find the user's systemd instance:

```bash
sudo machinectl shell artsite@
# or: sudo -iu artsite            …then, if systemctl --user can't connect:
#     export XDG_RUNTIME_DIR=/run/user/$(id -u)
```

From here on, **everything runs in that `artsite` session** — `git clone`,
`install.sh`, `podman build`, `systemctl --user`, `podman exec` — *except* the
`sudo mkdir/chown` host-prep lines in §3 and the host Caddy config in §5, which
are root's. Grab the code:

```bash
git clone https://github.com/<you>/artsite.git ~/artsite
cd ~/artsite
```

Debian notes:

- The `:z`/`:Z` volume suffixes in the quadlets are **SELinux** labels. Debian
  uses AppArmor, so they're harmless no-ops — leave them (they matter on
  Fedora-family hosts).
- Rootless Podman can't bind ports below 1024, which is fine here: the app
  publishes only `127.0.0.1:8000`, and the host Caddy owns 80/443.
- Logs live in the *user* journal: `journalctl --user -u artsite.service -f`
  from inside the `artsite` session, or
  `sudo journalctl _UID="$(id -u artsite)"` from your admin account.

## 3. Host preparation

```bash
# Media directory — point this at your NAS mount. It must be writable by the
# Podman user (artsite, or your own user if you skipped §2) and readable by
# Caddy. /srv/artsite/media is the path baked into the quadlet
# (artsite.container) and the Caddyfile; change both if you use another path.
sudo mkdir -p /srv/artsite/media
sudo chown artsite:artsite /srv/artsite/media

# Private documents directory — staff-only files (receipts, valuations). Caddy
# must NOT serve this path and it must stay OFF the media tree above; only the
# app container touches it (mounted :Z, exclusive).
sudo mkdir -p /srv/artsite/private
sudo chown artsite:artsite /srv/artsite/private
sudo chmod 750 /srv/artsite/private        # nothing but the app should read it
```

**Rootless Podman + shared media dir — the gotchas:**

- **UID mapping.** Files the container writes are owned by a *subuid* on the host,
  not the `artsite` user itself. So Caddy can read them, either run Caddy as root
  (root can read any uid) or grant world read — Debian's `caddy` package runs as
  the unprivileged `caddy` user, so: `sudo chmod -R o+rX /srv/artsite/media`
  (files the container writes afterwards are world-readable by default, umask
  022). The `:z` (lowercase, shared) SELinux
  label on the volume in `artsite.container` lets a second process (Caddy) read
  the same dir; do **not** use `:Z` (uppercase, exclusive) — it relabels the dir
  for one container and Caddy then gets permission denied.
- **First write.** If the directory looks empty to Caddy after the app writes to
  it, check ownership with `podman unshare ls -l /srv/artsite/media`.
- **Keep private documents off the proxy.** `/srv/artsite/private` (mounted at
  `/data/private`) must never get a Caddy route — it holds staff-only documents
  served only through the app's gated download view. Unlike the media dir it is
  mounted `:Z` (exclusive); nothing else should read it.

## 4. Configure and start

Everything here runs **as the `artsite` user** (the §2 session), from the
`~/artsite` clone.

```bash
mkdir -p ~/.config/artsite
cp deploy/artsite.env.example ~/.config/artsite/artsite.env
# Edit it and set:
#   DJANGO_SECRET_KEY   (generate: python -c 'import secrets; print(secrets.token_urlsafe(50))')
#   POSTGRES_PASSWORD   (and the matching password in DATABASE_URL)
#   ALLOWED_HOSTS       (your public domain)
# STORAGE_BACKEND=local is set by the quadlet.

./deploy/install.sh                          # install the quadlet units
podman build -t artsite .                    # build the app image
systemctl --user start artsite-db.service    # start Postgres first
systemctl --user start artsite.service       # migrates the (empty) schema, then runs gunicorn
```

The app container runs `migrate` on start, so a fresh install comes up with an
empty, fully-migrated database. Create your first curator (a **staff** account —
the curate admin gates on `is_staff`), then optionally load sample data:

```bash
podman exec -it artsite python manage.py createsuperuser
podman exec artsite python manage.py seed_demo      # optional: a few sample pieces
```

> Moving an **existing** Fly.io + GCS collection onto this server instead of
> starting empty? Do §1–§3, then follow **§8** in place of this section.

## 5. Serve media via Caddy

Add the `/media/` handler from `deploy/Caddyfile.artsite` to your host Caddy
(`import /path/to/Caddyfile.artsite`), pointed at the same `/srv/artsite/media`
path. Reload Caddy. `file_server` does not list directories, so the collection
isn't browsable.

> **One trusted proxy hop only.** The login rate-limiter
> (`art/ratelimit.py`) treats the right-most `X-Forwarded-For` entry as the real
> client IP — correct when Caddy is the single proxy appending it (its default).
> If you put **Cloudflare, a CDN, or another WAF in front of Caddy**, that
> assumption breaks and the per-`(user, IP)` lockout can be bypassed or
> mis-attributed. Revisit `art/ratelimit.py` (and Caddy's `trusted_proxies`)
> before adding an extra hop.

## 6. Go live

Point DNS / your port-forward at the server, then smoke-test over your domain:

- `https://<your-domain>/` loads (thumbnails regenerate on first hit).
- A detail page shows the full image and an absolute `og:image`.
- Sign in at `/curate/`, upload a test image, confirm it appears under
  `/srv/artsite/media` and renders.

Start-on-boot needs no `systemctl enable`: Quadlet-generated units can't be
enabled by hand, but they carry `[Install] WantedBy=default.target`, so they
start whenever the user's systemd instance does — which linger provides
(`loginctl enable-linger artsite`, already done in §2). Verify with a reboot.

## 7. Ongoing operations

Back up **before** you have a collection worth losing — both the database and the
media (the private documents have no other copy).

- **Database backups** — `deploy/backup-db.sh` runs `pg_dump` from the container
  to a dated file on the NAS, pruning old ones. Schedule it with the systemd
  user units in `deploy/systemd/` (see that script's header). This is also the
  *only* safe way to get Postgres data onto the NAS — a logical dump, never the
  live data dir.
- **Media backups** — `deploy/backup-media.sh` rsyncs `/srv/artsite/media` **and**
  `/srv/artsite/private` to `MEDIA_BACKUP_DEST` (a second machine/disk; an
  `rsync`-over-ssh `user@host:/path` works). Additive by default so an accidental
  delete stays recoverable; `MIRROR=1` for a true mirror. The private documents
  (receipts, valuations) are irreplaceable and have no other copy, so don't skip
  them. Schedule with the `artsite-media-backup.{service,timer}` units in
  `deploy/systemd/`. Keep at least one **off-site** copy — the NAS is a single
  point of failure.
- **Restore drill** — `deploy/restore-drill.sh` restores the latest DB dump into a
  throwaway Postgres container and runs `verify_media` against the media backup, so
  you learn the backups are *usable together* — not just present — before you need
  them. Schedule monthly with `artsite-restore-drill.{service,timer}`; run it by
  hand once first to confirm the image name / mount paths on your host.
- **Orphan cleanup** — `podman exec artsite python manage.py cleanup_orphan_images`
  lists media files no live row references; add `--delete` to remove them
  (dry-run by default). Works identically against local storage.
- **Forgot the curator password?** — `podman exec -it artsite python manage.py
  changepassword <username>` (the account must be `is_staff`). Clear a
  django-axes lockout with `podman exec artsite python manage.py axes_reset`.

## 8. Migrating an existing Fly.io + GCS deployment

Only if you're moving a live Fly/GCS collection onto this server (instead of the
fresh start in §4). Do this with a **fresh dump of the live data**, not an old
backup lying around — that's a stale historical snapshot.

### 8a. Copy media down from GCS

The DB stores bare relative names (e.g. `<uuid>-photo.jpg`); the bucket prefix is
added by the GCS backend, so files must land **directly** under `MEDIA_ROOT`:

```bash
# Brings down both source images and existing thumbnails.
gsutil -m rsync -r gs://<your-bucket>/art /srv/artsite/media
```

Copy the **private documents** down separately. They live in a sibling prefix
(`art-private`), deliberately NOT under `art/`, so the rsync above never touches
them — copy them into the private dir, never into the media tree:

```bash
gsutil -m rsync -r gs://<your-bucket>/art-private /srv/artsite/private
```

### 8b. Dump the production database from Fly

```bash
# In one terminal: open a tunnel to the Fly Postgres app
fly proxy 5433:5432 -a <your-pg-app>

# In another: take a custom-format dump (parallelisable, selective restore)
pg_dump -Fc -U postgres -h localhost -p 5433 artsite > artsite.dump
```

### 8c. Configure + start the DB (as in §4), then restore into it

Do the §4 env + `install.sh` + `podman build` + start `artsite-db.service` steps,
then restore the dump **before** starting the app:

```bash
podman cp artsite.dump artsite-db:/tmp/artsite.dump
podman exec -i artsite-db \
  pg_restore --no-owner --no-acl --clean --if-exists \
  -U artsite -d artsite /tmp/artsite.dump
```

`--no-owner --no-acl` drops the Fly-only `flypgadmin`/`artsite` ownership and
GRANTs (the role won't exist locally); objects become owned by the connecting
`artsite` role. UUIDs are app-generated, so **no Postgres extensions are needed**.

```bash
systemctl --user start artsite.service       # runs `migrate` on start, then gunicorn
```

A fresh Fly dump is already at the current migration, so `migrate` is a no-op.

### 8d. Regenerate thumbnails

The restored `easy_thumbnails_*` rows reference GCS-flavoured storage; clear them
so thumbnails regenerate locally on demand:

```bash
podman exec -i artsite-db psql -U artsite -d artsite -c \
  "TRUNCATE easy_thumbnails_thumbnail, easy_thumbnails_source, easy_thumbnails_thumbnaildimensions RESTART IDENTITY;"
```

### 8e. Verify before cutover

Confirm every image the database references is actually on disk:

```bash
podman exec artsite python manage.py verify_media
```

It exits non-zero and lists any missing files (with model/pk). Do not cut over
until it passes.

**Rollback:** the deployments are independent. To revert, point DNS back at Fly
(still `STORAGE_BACKEND=gcs`). To run the *self-host* against GCS temporarily, set
`STORAGE_BACKEND=gcs` in `artsite.container` and provide a key (see §9).

## 9. Running on Fly.io / GCS instead

Copy `fly.toml.example` to `fly.toml` (gitignored) and set your app name, GCS
bucket/project, and hostname(s); it sets `STORAGE_BACKEND=gcs`. The
service-account key is **not** baked into the image (see `.dockerignore`);
provide it as a Fly secret, written to the container by the `[[files]]` block
in `fly.toml`:

```bash
fly secrets set GCS_CREDENTIALS="$(base64 < your-gcs-key.json)"
fly deploy
```

> Note: this Fly secret wiring has not been validated against a live Fly deploy;
> confirm it before relying on it.
