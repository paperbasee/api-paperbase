from django.db import migrations, models


def delete_pathao_couriers(apps, schema_editor):
    Courier = apps.get_model("couriers", "Courier")
    Courier.objects.filter(provider="pathao").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("couriers", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_pathao_couriers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="courier",
            name="access_token_encrypted",
        ),
        migrations.RemoveField(
            model_name="courier",
            name="refresh_token",
        ),
        migrations.RemoveField(
            model_name="courier",
            name="token_expires_at",
        ),
        migrations.AlterField(
            model_name="courier",
            name="provider",
            field=models.CharField(
                choices=[("steadfast", "Steadfast")],
                max_length=20,
            ),
        ),
    ]
