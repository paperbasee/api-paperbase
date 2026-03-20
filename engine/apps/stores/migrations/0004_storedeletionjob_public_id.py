import engine.core.ids
from django.db import migrations, models


def populate_storedeletionjob_public_ids(apps, schema_editor):
    StoreDeletionJob = apps.get_model("stores", "StoreDeletionJob")
    for job in StoreDeletionJob.objects.filter(public_id=""):
        job.public_id = engine.core.ids.generate_public_id("storedeletionjob")
        job.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0003_rename_stores_storedeletionjob_user_statu_idx_stores_stor_user_id_f7a254_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="storedeletionjob",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                editable=False,
                help_text="Non-sequential public identifier (e.g. dlj_xxx).",
                max_length=32,
            ),
        ),
        migrations.RunPython(
            populate_storedeletionjob_public_ids,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="storedeletionjob",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. dlj_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
    ]
