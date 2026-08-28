"""Find — and, with --delete, remove — image files in the configured storage
backend that no live Piece or Artist references.

These are orphans left behind by deletions made before thumbnail cleanup was
wired into the delete path (a deleted piece's source image was removed, but its
generated thumbnails were not). This command reconciles storage against the
database.

It can delete from production storage (a GCS bucket or a local media
directory), so it is built to make data loss effectively impossible:

* Dry run by default — listing only; deletion requires --delete.
* The in-use set is discovered by introspecting every FileField/ImageField in
  the art app, so a future image-bearing model is covered automatically (not
  just Piece.image / Artist.portrait).
* Refuses to run when the database references no images at all (otherwise every
  file would look orphaned). Override only with --allow-empty-db.
* A file is treated as in use — and never deleted — if it is a live source
  image *or any thumbnail/derivative of one* (easy-thumbnails names thumbnails
  "<source>.<options>"). Classification errs toward keeping.
* Refuses to run under a non-default easy-thumbnails layout (a changed
  THUMBNAIL_NAMER or a base/sub-dir/prefix), since the "<source>." rule would
  no longer recognise live thumbnails.
* Skips files modified within --min-age-days (default 1) to avoid racing an
  in-flight upload or a just-generated thumbnail. Negative values are rejected.
* Re-checks liveness against the database immediately before each delete.
"""

from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import models as dj_models
from django.utils import timezone


class Command(BaseCommand):
    help = 'List (and with --delete, remove) orphaned image files in the configured storage backend.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Delete the orphans. Without this flag the command only lists them (dry run).',
        )
        parser.add_argument(
            '--min-age-days',
            type=float,
            default=1.0,
            help='Never touch files modified within this many days — guards against deleting an in-flight '
            'upload or a just-generated thumbnail. Default: 1.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Process at most this many orphan candidates (name-sorted; the first N).',
        )
        parser.add_argument(
            '--no-input',
            action='store_true',
            help='Do not prompt for confirmation before deleting.',
        )
        parser.add_argument(
            '--allow-empty-db',
            action='store_true',
            help='Proceed even when the database references no images. DANGEROUS: with no live images every '
            'file in storage looks orphaned.',
        )

    def handle(self, *args, **opts):
        if opts['min_age_days'] < 0:
            raise CommandError(
                '--min-age-days cannot be negative — it would move the safety cutoff into the future and make '
                'even just-created files eligible for deletion.'
            )
        self._require_default_thumbnail_layout()

        storage = default_storage

        sources = self._in_use_sources()
        self.stdout.write(f'In-use source images referenced by the database: {len(sources)}')
        if not sources and not opts['allow_empty_db']:
            raise CommandError(
                'No in-use images found in the database — refusing to run, because with no live images every '
                'file in storage would look orphaned. If the collection genuinely has no images, re-run '
                'with --allow-empty-db.'
            )

        try:
            all_files = self._walk(storage)
        except Exception as exc:
            raise CommandError(f'Could not list the storage backend: {exc}') from exc
        self.stdout.write(f'Files in storage: {len(all_files)}')

        orphans = sorted(name for name in all_files if not self._belongs_to_live_source(name, sources))
        self.stdout.write(self.style.WARNING(f'Orphan candidates (referenced by no live source): {len(orphans)}'))
        if not orphans:
            self.stdout.write(self.style.SUCCESS('Nothing to do — every file belongs to a live source.'))
            return

        deletable, skipped_recent, unknown_age = self._apply_age_guard(storage, orphans, opts['min_age_days'])
        if opts['limit'] is not None:
            deletable = deletable[: opts['limit']]

        total_bytes = self._sum_sizes(storage, deletable)
        self._report(deletable, skipped_recent, unknown_age, total_bytes, opts)

        if not opts['delete']:
            self.stdout.write(
                self.style.NOTICE('\nDry run — nothing deleted. Re-run with --delete to remove the files above.')
            )
            return
        if not deletable:
            return

        if not opts['no_input']:
            prompt = f"\nDelete {len(deletable)} file(s) ({self._human(total_bytes)})? Type 'yes' to proceed: "
            try:
                answer = input(prompt)
            except EOFError:
                raise CommandError('Aborted — no input available. Pass --no-input to run non-interactively.') from None
            if answer.strip() != 'yes':
                raise CommandError('Aborted — nothing deleted.')

        deleted = self._delete(storage, deletable)
        self.stdout.write(self.style.SUCCESS(f'\nDeleted {deleted} orphaned file(s).'))

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require_default_thumbnail_layout():
        """The orphan classifier assumes easy-thumbnails' default naming —
        thumbnails named '<source>.<options>' stored beside their source. If the
        project ever switches THUMBNAIL_NAMER or sets a base/sub-dir or prefix, a
        live source's thumbnails would no longer be recognised as in-use and
        could be deleted. Refuse to run rather than risk that."""
        namer = getattr(settings, 'THUMBNAIL_NAMER', 'easy_thumbnails.namers.default')
        if namer != 'easy_thumbnails.namers.default':
            raise CommandError(
                f'THUMBNAIL_NAMER is {namer!r}, not the default source-prefixed namer this command relies on '
                'to recognise a live source’s thumbnails. Refusing to run so they can’t be misread as orphans.'
            )
        for name in ('THUMBNAIL_BASEDIR', 'THUMBNAIL_SUBDIR', 'THUMBNAIL_PREFIX'):
            if getattr(settings, name, ''):
                raise CommandError(
                    f'{name} is set; this command assumes thumbnails sit beside their source under the default '
                    'namer. Refusing to run.'
                )

    @staticmethod
    def _in_use_sources():
        """Every source-file name a live row in the *art* app points at, found by
        introspecting all FileField/ImageField columns — so a future image-bearing
        model is covered automatically, not just Piece.image / Artist.portrait.
        Empty/null values are skipped: an empty name must never become a
        match-everything prefix."""
        names = set()
        for model in apps.get_app_config('art').get_models():
            # Only fields in the default (public) store: this command walks and
            # deletes from that store, so private-store fields (e.g. a piece's
            # documents) live elsewhere and must never be considered here.
            fields = [
                f.name
                for f in model._meta.get_fields()
                if isinstance(f, dj_models.FileField) and f.storage is default_storage
            ]
            if not fields:
                continue
            for row in model.objects.values(*fields):
                for field in fields:
                    value = row.get(field)
                    if value:
                        names.add(value)
        return names

    def _walk(self, storage, path=''):
        """All file names under `path`, recursing into any subdirectories. The
        storage layout is flat today, but recursing means a nested file is found
        (and classified) rather than silently ignored."""
        files = []
        dirs, names = storage.listdir(path)
        for name in names:
            if not name:  # a GCS directory-placeholder blob ('…/') surfaces here as ''
                continue
            files.append(f'{path}/{name}' if path else name)
        for subdir in dirs:
            files.extend(self._walk(storage, f'{path}/{subdir}' if path else subdir))
        return files

    @staticmethod
    def _belongs_to_live_source(name, sources):
        """True if `name` is a live source image or a thumbnail/derivative of
        one. easy-thumbnails names thumbnails '<source>.<options>', so a file
        belongs to a source when it equals it or starts with "<source>.".
        Matched on the basename too, so a file in a (hypothetical) thumbnail
        subdirectory can't be misread as an orphan. Deliberately errs toward
        True: a false "in use" only leaves an orphan behind, while a false
        "orphan" would delete a file in use."""
        base = name.rsplit('/', 1)[-1]
        if name in sources or base in sources:
            return True
        return any(name.startswith(f'{s}.') or base.startswith(f'{s}.') for s in sources)

    @staticmethod
    def _apply_age_guard(storage, orphans, min_age_days):
        cutoff = timezone.now() - timedelta(days=min_age_days)
        deletable, skipped_recent, unknown_age = [], [], []
        for name in orphans:
            try:
                mtime = storage.get_modified_time(name)
            except Exception:
                unknown_age.append(name)  # can't tell its age — keep it, to be safe
                continue
            if timezone.is_naive(mtime):
                mtime = timezone.make_aware(mtime, timezone.get_default_timezone())
            (skipped_recent if mtime > cutoff else deletable).append(name)
        return deletable, skipped_recent, unknown_age

    @staticmethod
    def _sum_sizes(storage, names):
        total = 0
        for name in names:
            try:
                total += storage.size(name)
            except Exception:
                pass
        return total

    def _report(self, deletable, skipped_recent, unknown_age, total_bytes, opts):
        verb = 'To delete' if opts['delete'] else 'Would delete'
        self.stdout.write(f'\n{verb}: {len(deletable)} file(s), {self._human(total_bytes)}')
        for name in deletable:
            self.stdout.write(f'  - {name}')

        # List the orphans we are deliberately keeping (by name) so a dry run
        # shows the full picture, not just an empty "to delete" list.
        kept = [(n, f'newer than {opts["min_age_days"]}d') for n in skipped_recent]
        kept += [(n, 'modified time unreadable') for n in unknown_age]
        if kept:
            self.stdout.write(self.style.NOTICE(f'\nKept {len(kept)} orphan(s) as a safety measure:'))
            for name, why in kept:
                self.stdout.write(f'  - {name}  ({why})')

    def _delete(self, storage, names):
        # Final guard: re-read the live set right before deleting, so a piece or
        # artist created during the run can't have its file removed.
        live = self._in_use_sources()
        deleted = 0
        for name in names:
            if self._belongs_to_live_source(name, live):
                self.stdout.write(self.style.WARNING(f'  skip (now referenced by a live source): {name}'))
                continue
            try:
                storage.delete(name)
            except Exception as exc:
                self.stderr.write(f'  ERROR deleting {name}: {exc}')
                continue
            deleted += 1
            self.stdout.write(f'  deleted: {name}')
        return deleted

    @staticmethod
    def _human(num_bytes):
        size = float(num_bytes)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if size < 1024 or unit == 'TB':
                return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
            size /= 1024
