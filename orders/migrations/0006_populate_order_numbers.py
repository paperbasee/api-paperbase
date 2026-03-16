# Data migration: assign sequential order numbers to existing orders

from django.db import migrations


def populate_order_numbers(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    OrderNumberCounter = apps.get_model('orders', 'OrderNumberCounter')

    orders = Order.objects.order_by('created_at')
    next_value = 1

    for order in orders:
        # Format: 8 digits until 99999999, then 9, 10, ...
        order_number = str(next_value).zfill(max(8, len(str(next_value))))
        order.order_number = order_number
        order.save()
        next_value += 1

    # Ensure counter exists and is set for future orders
    counter, created = OrderNumberCounter.objects.get_or_create(
        pk=1, defaults={'next_value': 1}
    )
    counter.next_value = next_value
    counter.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_ordernumbercounter_order_order_number'),
    ]

    operations = [
        migrations.RunPython(populate_order_numbers, noop),
    ]
