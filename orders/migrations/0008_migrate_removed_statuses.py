# Generated migration to convert removed statuses (processing, shipped, delivered) to confirmed

from django.db import migrations


def migrate_statuses(apps, schema_editor):
    """Convert processing, shipped, delivered to confirmed."""
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(status__in=['processing', 'shipped', 'delivered']).update(status='confirmed')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0004_order_district'),
    ]

    operations = [
        migrations.RunPython(migrate_statuses, noop),
    ]
