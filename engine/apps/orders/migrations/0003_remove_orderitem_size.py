from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_add_extra_data_jsonb"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="orderitem",
            name="size",
        ),
    ]

