from django.db import migrations, models


def _populate_stockmovement_public_ids(apps, schema_editor):
    from engine.core.ids import generate_public_id

    StockMovement = apps.get_model("inventory", "StockMovement")
    for movement in StockMovement.objects.filter(public_id__isnull=True).iterator():
        pid = generate_public_id("stockmovement")
        while StockMovement.objects.filter(public_id=pid).exists():
            pid = generate_public_id("stockmovement")
        movement.public_id = pid
        movement.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(_populate_stockmovement_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="stockmovement",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. stm_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
    ]
