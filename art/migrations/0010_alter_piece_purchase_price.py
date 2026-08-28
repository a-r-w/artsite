# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('art', '0009_alter_piece_slug'),
    ]

    operations = [
        migrations.AlterField(
            model_name='piece',
            name='purchase_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
