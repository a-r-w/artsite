"""Give stranded captures their real URL under the relaxed settling rule.

0029 settled a piece's slug only once it was published under a real artist, so a
capture that had been titled but not yet fully filed kept the throwaway
"quick-add-<hex>" (earlier: "capture-<hex>") address Quick add gave it. A title
is now enough to commit a URL, so those pieces are re-slugged here rather than
being left on a throwaway address until something happens to save them again.

Scoped deliberately narrowly — ONLY rows still sitting on a throwaway address:

* A piece with a human-meaningful slug is left alone even if it is unsettled.
  Rewriting it would change a URL that is already in the world (bookmarks, search
  results, an NFC tag), and this migration has no way to redirect the old one.
  The case that bites: 0029 flags any artist literally named "Not set" as the
  placeholder, so a curator who files unattributed works that way would otherwise
  have every one of those pieces silently re-addressed at deploy time.
* Tagged pieces are skipped outright: a tag on the wall encodes the current URL.

The slug derivation is duplicated from Piece._slug_base()/_settled_slug() because
migrations run against historical models, which have no custom save(). Keep the
two in step if the naming scheme changes — including the "title must survive
slugify()" rule, which is why the loop skips such rows rather than settling them
on the 'piece' fallback.
"""

from django.db import migrations
from django.template.defaultfilters import slugify

# Quick add has used both prefixes; both are throwaway addresses, never shared.
THROWAWAY_PREFIXES = ('quick-add-', 'capture-')


def resettle(apps, schema_editor):
    Piece = apps.get_model('art', 'Piece')
    candidates = (
        Piece.objects.select_related('artist')
        .filter(slug_settled=False, tagged=False)
        .exclude(title='')
    )
    stranded = [p for p in candidates if p.slug.startswith(THROWAWAY_PREFIXES)]
    if not stranded:
        return

    taken = set(Piece.objects.values_list('slug', flat=True))
    for piece in stranded:
        title_part = slugify(piece.title)
        if not title_part:
            continue  # matches the model: no usable title, so no URL to commit
        taken.discard(piece.slug)  # its own throwaway slug is free to give up
        artist_part = '' if piece.artist.is_placeholder else slugify(piece.artist.name)
        base = '-'.join(part for part in (artist_part, title_part) if part)
        slug, counter = base, 0
        while slug in taken:
            counter += 1
            slug = f'{base}-{counter}'
        taken.add(slug)
        piece.slug = slug
        piece.slug_settled = True
        piece.save(update_fields=['slug', 'slug_settled'])


def noop(apps, schema_editor):
    """Irreversible in practice: the previous slugs aren't recorded anywhere."""


class Migration(migrations.Migration):
    dependencies = [('art', '0029_settle_existing_slugs')]
    operations = [migrations.RunPython(resettle, noop)]
