# Generated manually - remove store description field

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0004_store_owner_domain_timezone_changes"),
    ]

    operations = [
        migrations.RemoveField(model_name="store", name="description"),
    ]
