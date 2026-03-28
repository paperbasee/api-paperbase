from django.db import migrations, models


def prune_non_product_schema(apps, schema_editor):
    StoreSettings = apps.get_model("stores", "StoreSettings")
    for row in StoreSettings.objects.all():
        raw = row.extra_field_schema
        if not isinstance(raw, list):
            continue
        kept = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            entity = item.get("entityType") or item.get("entity_type") or "product"
            if str(entity).lower() == "product":
                kept.append(item)
        if len(kept) != len(raw):
            row.extra_field_schema = kept
            row.save(update_fields=["extra_field_schema"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0002_alter_storesettings_modules_enabled"),
    ]

    operations = [
        migrations.RunPython(prune_non_product_schema, noop_reverse),
        migrations.AlterField(
            model_name="storesettings",
            name="extra_field_schema",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Extra field definitions for products only: [{id, entityType, name, fieldType, required, order, options}]",
            ),
        ),
    ]
