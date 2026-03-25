from django.db import migrations, models


def _populate_activitylog_public_ids(apps, schema_editor):
    from engine.core.ids import generate_public_id

    ActivityLog = apps.get_model("core", "ActivityLog")
    for row in ActivityLog.objects.filter(public_id__isnull=True).iterator():
        pid = generate_public_id("activitylog")
        while ActivityLog.objects.filter(public_id=pid).exists():
            pid = generate_public_id("activitylog")
        row.public_id = pid
        row.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_activitylog_add_store_fk"),
    ]

    operations = [
        # No db_index here: AlterField(unique=True) below creates the unique btree + PG _like index.
        # AddField(..., db_index=True) + AlterField(unique=True) would duplicate the _like index and fail.
        migrations.AddField(
            model_name="activitylog",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=False,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(_populate_activitylog_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="activitylog",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. act_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
    ]
