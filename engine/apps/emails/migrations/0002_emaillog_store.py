from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0001_initial"),
        ("stores", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaillog",
            name="store",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="email_logs",
                to="stores.store",
            ),
        ),
    ]
