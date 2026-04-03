from django.db import migrations


def backfill_orderitem_snapshots(apps, schema_editor):
    OrderItem = apps.get_model("orders", "OrderItem")
    ProductVariantAttribute = apps.get_model("products", "ProductVariantAttribute")

    def variant_snapshot_value(item):
        if not item.variant_id or not item.variant:
            return None
        links = (
            ProductVariantAttribute.objects.filter(variant_id=item.variant_id)
            .select_related("attribute_value__attribute")
            .order_by("attribute_value__attribute__order", "attribute_value__order")
        )
        labels = [
            f"{link.attribute_value.attribute.name}: {link.attribute_value.value}"
            for link in links
        ]
        if labels:
            return ", ".join(labels)
        return getattr(item.variant, "sku", None) or item.variant.public_id

    for item in OrderItem.objects.select_related("product", "variant").all().iterator():
        product_name_snapshot = (
            item.product.name if item.product_id and item.product else "Unavailable"
        )
        variant_snapshot = variant_snapshot_value(item)
        unit_price_snapshot = item.unit_price
        OrderItem.objects.filter(pk=item.pk).update(
            product_name_snapshot=product_name_snapshot,
            variant_snapshot=variant_snapshot,
            unit_price_snapshot=unit_price_snapshot,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_orderitem_snapshots_add_nullable"),
    ]

    operations = [
        migrations.RunPython(backfill_orderitem_snapshots, migrations.RunPython.noop),
    ]
