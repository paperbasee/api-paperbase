from django.db import migrations, models


def forward_open_to_new(apps, schema_editor):
    SupportTicket = apps.get_model("support", "SupportTicket")
    SupportTicket.objects.filter(status="open").update(status="new")


def backward_new_to_open(apps, schema_editor):
    SupportTicket = apps.get_model("support", "SupportTicket")
    SupportTicket.objects.filter(status="new").update(status="open")


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0002_add_public_id_to_attachment"),
    ]

    operations = [
        migrations.RunPython(forward_open_to_new, backward_new_to_open),
        migrations.AlterField(
            model_name="supportticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("in_progress", "In progress"),
                    ("resolved", "Resolved"),
                    ("closed", "Closed"),
                ],
                default="new",
                max_length=20,
            ),
        ),
    ]
