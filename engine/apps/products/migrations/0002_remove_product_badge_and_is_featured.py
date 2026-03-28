from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="product",
            name="badge",
        ),
        migrations.RemoveField(
            model_name="product",
            name="is_featured",
        ),
    ]
