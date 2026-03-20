from django.db import migrations, models


def _populate_wishlist_public_ids(apps, schema_editor):
    from engine.core.ids import generate_public_id

    WishlistItem = apps.get_model("wishlist", "WishlistItem")
    for item in WishlistItem.objects.filter(public_id__isnull=True).iterator():
        pid = generate_public_id("wishlist")
        while WishlistItem.objects.filter(public_id=pid).exists():
            pid = generate_public_id("wishlist")
        item.public_id = pid
        item.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("wishlist", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="wishlistitem",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(_populate_wishlist_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="wishlistitem",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. wsh_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
    ]
