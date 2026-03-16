# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0003_alter_order_delivery_area_alter_order_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='district',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
