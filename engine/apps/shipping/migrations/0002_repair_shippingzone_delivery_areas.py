"""Repair DBs where shipping_shippingzone predates delivery_areas (edited 0001 drift)."""

from django.db import migrations


def add_delivery_areas_if_missing(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        desc = connection.introspection.get_table_description(
            cursor, "shipping_shippingzone"
        )
    if any(col.name == "delivery_areas" for col in desc):
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE shipping_shippingzone ADD COLUMN delivery_areas "
            "varchar(100) NOT NULL DEFAULT ''"
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shipping", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_delivery_areas_if_missing, noop_reverse),
    ]
