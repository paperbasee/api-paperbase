# Generated manually — verify existing category rows respect max depth and no cycles.

from django.db import migrations


def _assert_category_tree_valid(apps, schema_editor):
    Category = apps.get_model("products", "Category")
    max_depth = 5
    for cat in Category.objects.all().iterator():
        depth = 1
        current_id = cat.parent_id
        seen = {cat.pk}
        while current_id:
            if current_id in seen:
                raise ValueError(
                    f"products.Category pk={cat.pk}: cycle detected in category hierarchy."
                )
            seen.add(current_id)
            depth += 1
            if depth > max_depth:
                raise ValueError(
                    f"products.Category pk={cat.pk}: depth exceeds {max_depth} levels. "
                    "Fix in Django admin or shell before applying this migration."
                )
            parent = Category.objects.filter(pk=current_id).first()
            if parent is None:
                break
            current_id = parent.parent_id


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0003_drop_reviews_table"),
    ]

    operations = [
        migrations.RunPython(_assert_category_tree_valid, noop_reverse),
    ]
