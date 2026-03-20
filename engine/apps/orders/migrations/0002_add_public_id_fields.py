from django.db import migrations, models


def _populate_order_public_ids(apps, schema_editor):
    from engine.core.ids import generate_public_id

    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")

    for order in Order.objects.filter(public_id__isnull=True).iterator():
        pid = generate_public_id("order")
        while Order.objects.filter(public_id=pid).exists():
            pid = generate_public_id("order")
        order.public_id = pid
        order.save(update_fields=["public_id"])

    for item in OrderItem.objects.filter(public_id__isnull=True).iterator():
        pid = generate_public_id("orderitem")
        while OrderItem.objects.filter(public_id=pid).exists():
            pid = generate_public_id("orderitem")
        item.public_id = pid
        item.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(_populate_order_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="order",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. ord_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. oit_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
    ]
