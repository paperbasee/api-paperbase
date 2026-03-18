# Feature gate: migrate features JSON structure, add is_default, remove max_stores

from django.db import migrations, models


def migrate_features_and_set_default(apps, schema_editor):
    """Transform flat features to {limits, features} structure; set is_default on lowest-price plan."""
    Plan = apps.get_model("billing", "Plan")
    plans = list(Plan.objects.all().order_by("price"))
    if not plans:
        return

    for plan in plans:
        old_features = plan.features or {}
        max_stores = getattr(plan, "max_stores", 1)

        # Build new structure: { limits: {...}, features: {...} }
        if isinstance(old_features.get("limits"), dict):
            limits = dict(old_features["limits"])
        else:
            limits = {"max_stores": max_stores}

        if isinstance(old_features.get("features"), dict):
            features = dict(old_features["features"])
        else:
            # Migrate from flat structure
            features = {}
            for k, v in old_features.items():
                if k != "limits" and k != "features":
                    features[k] = bool(v) if isinstance(v, bool) else v
            if "advanced_analytics" not in features:
                features["advanced_analytics"] = False
            if "marketing_tools" not in features:
                features["marketing_tools"] = False

        plan.features = {"limits": limits, "features": features}
        plan.save()

    # Set is_default on lowest-price plan (fallback for users without subscription)
    default_plan = plans[0]
    default_plan.is_default = True
    default_plan.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_rename_billing_pay_user_id_7a8b9c_idx_billing_pay_user_id_09fdcc_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="is_default",
            field=models.BooleanField(default=False, help_text="Used as fallback when user has no active subscription."),
        ),
        migrations.RunPython(migrate_features_and_set_default, noop),
        migrations.RemoveField(
            model_name="plan",
            name="max_stores",
        ),
    ]
