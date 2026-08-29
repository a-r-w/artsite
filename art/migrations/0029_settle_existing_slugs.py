"""Backfill the state the new slug rules depend on.

Two facts about pre-existing data that the schema defaults get wrong:

* Every published piece already has a public URL that people may have bookmarked
  or written onto an NFC tag, so its slug must be treated as settled. Left at the
  field default (False) the next save would re-mint it.
* A curator running this upgrade may already have "Not set" rows from the earlier
  build of Quick add, which identified the placeholder by name. Flag them so they
  are recognised (and reused) rather than duplicated.
"""

from django.db import migrations


def settle_existing(apps, schema_editor):
    # Flag the placeholders FIRST: whether a piece counts as settled depends on
    # whether its artist is one.
    for model, field in (('Artist', 'name'), ('Location', 'description')):
        rows = apps.get_model('art', model).objects.filter(**{field: 'Not set'}).order_by('pk')
        # The partial unique index permits only one flagged row per model; if a
        # collection somehow has several, flag the first and leave the rest as
        # ordinary records for the curator to merge.
        first = rows.first()
        if first is not None:
            rows.model.objects.filter(pk=first.pk).update(is_placeholder=True)

    # Mirror Piece._identity_is_complete(): a piece owns its URL once it is
    # published, titled, and under a real artist. Anything short of that is an
    # unfinished capture whose URL is still provisional — settling it here would
    # freeze a placeholder-derived slug permanently, which is the very bug this
    # scheme exists to prevent.
    Piece = apps.get_model('art', 'Piece')
    Piece.objects.filter(draft=False, artist__is_placeholder=False).exclude(title='').update(slug_settled=True)


def unsettle(apps, schema_editor):
    Piece = apps.get_model('art', 'Piece')
    Piece.objects.update(slug_settled=False)
    for model in ('Artist', 'Location'):
        apps.get_model('art', model).objects.update(is_placeholder=False)


class Migration(migrations.Migration):
    dependencies = [('art', '0028_artist_is_placeholder_location_is_placeholder_and_more')]
    operations = [migrations.RunPython(settle_existing, unsettle)]
