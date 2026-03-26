from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StoreApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.CharField(db_index=True, editable=False, help_text="Non-sequential public identifier (e.g. sak_xxx).", max_length=32, unique=True)),
                ("key_hash", models.CharField(db_index=True, help_text="SHA-256 hash of the API key material.", max_length=64, unique=True)),
                ("key_prefix", models.CharField(blank=True, default="", max_length=16)),
                ("key_last4", models.CharField(blank=True, default="", max_length=4)),
                ("label", models.CharField(blank=True, default="", max_length=80)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="api_keys", to="stores.store")),
            ],
            options={
                "indexes": [models.Index(fields=["store", "is_active", "created_at"], name="stores_stor_store_i_2527eb_idx")],
                "constraints": [models.UniqueConstraint(condition=models.Q(("is_active", True)), fields=("store",), name="one_active_store_api_key")],
            },
        ),
    ]
