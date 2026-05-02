from django.db import migrations


def rename_obsidian_to_noir(apps, schema_editor):
    StorefrontTheme = apps.get_model("theming", "StorefrontTheme")
    StorefrontTheme.objects.filter(palette="obsidian").update(palette="noir")


def reverse_noir_to_obsidian(apps, schema_editor):
    StorefrontTheme = apps.get_model("theming", "StorefrontTheme")
    StorefrontTheme.objects.filter(palette="noir").update(palette="obsidian")


class Migration(migrations.Migration):

    dependencies = [
        ("theming", "0002_backfill_storefront_themes"),
    ]

    operations = [
        migrations.RunPython(rename_obsidian_to_noir, reverse_noir_to_obsidian),
    ]
