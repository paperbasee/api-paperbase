# Generated manually for Store model changes

from django.db import migrations, models


def backfill_owner_from_membership(apps, schema_editor):
    """Set owner_name and owner_email from first owner membership for existing stores."""
    Store = apps.get_model("stores", "Store")
    StoreMembership = apps.get_model("stores", "StoreMembership")
    User = apps.get_model("auth", "User")

    for store in Store.objects.all():
        membership = (
            StoreMembership.objects.filter(store=store, role="owner")
            .order_by("created_at")
            .first()
        )
        if membership:
            user = User.objects.get(pk=membership.user_id)
            parts = [user.first_name or "", user.last_name or ""]
            full_name = " ".join(p for p in parts if p).strip()
            store.owner_name = full_name or user.username or user.email or "Owner"
            store.owner_email = user.email or "noreply@example.com"
        else:
            store.owner_name = store.owner_name or "Owner"
            store.owner_email = store.owner_email or "noreply@example.com"
        store.save()


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0003_alter_store_admin_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="store",
            name="owner_name",
            field=models.CharField(default="", max_length=255),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name="store",
            name="owner_email",
            field=models.EmailField(default="noreply@example.com", max_length=254),
            preserve_default=True,
        ),
        migrations.RunPython(backfill_owner_from_membership, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="store",
            name="owner_name",
            field=models.CharField(help_text="Full name of the store owner.", max_length=255),
        ),
        migrations.AlterField(
            model_name="store",
            name="owner_email",
            field=models.EmailField(help_text="Email address of the store owner.", max_length=254),
        ),
        # Alter domain to nullable
        migrations.AlterField(
            model_name="store",
            name="domain",
            field=models.CharField(
                blank=True,
                help_text="Full domain or host used to route requests to this store. Set via Settings > Networking.",
                max_length=255,
                null=True,
                unique=True,
            ),
        ),
        # Remove timezone, admin_name, admin_subtitle
        migrations.RemoveField(model_name="store", name="timezone"),
        migrations.RemoveField(model_name="store", name="admin_name"),
        migrations.RemoveField(model_name="store", name="admin_subtitle"),
    ]
