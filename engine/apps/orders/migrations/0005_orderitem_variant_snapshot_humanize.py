from django.db import migrations


def humanize_variant_snapshots(apps, schema_editor):
    OrderItem = apps.get_model("orders", "OrderItem")
    ProductVariantAttribute = apps.get_model("products", "ProductVariantAttribute")

    for item in (
        OrderItem.objects.select_related("variant")
        .filter(variant_id__isnull=False)
        .iterator()
    ):
        variant = item.variant
        if variant is None:
            continue
        links = (
            ProductVariantAttribute.objects.filter(variant_id=item.variant_id)
            .select_related("attribute_value__attribute")
            .order_by("attribute_value__attribute__order", "attribute_value__order")
        )
        labels = [
            f"{link.attribute_value.attribute.name}: {link.attribute_value.value}"
            for link in links
        ]
        if not labels:
            continue
        human_text = ", ".join(labels)
        # Only normalize snapshots that still look like identity fallbacks.
        if item.variant_snapshot in {None, "", variant.sku, variant.public_id}:
            OrderItem.objects.filter(pk=item.pk).update(variant_snapshot=human_text)


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0004_orderitem_snapshots_set_not_null"),
    ]

    operations = [
        migrations.RunPython(humanize_variant_snapshots, migrations.RunPython.noop),
    ]
