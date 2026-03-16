# Make order_number non-nullable after data migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_populate_order_numbers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='order_number',
            field=models.CharField(db_index=True, editable=False, max_length=20, unique=True),
        ),
    ]
