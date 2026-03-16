"""
Migration: Introduce NavbarCategory model.

Transforms the self-referential Category hierarchy into a two-model structure:
  - NavbarCategory  (was: top-level Category with parent=null)
  - Category        (was: child Category with parent set — now purely subcategories)

Product.category FK is moved from Category → NavbarCategory.

Steps
-----
1.  Create NavbarCategory table.
2.  Add Category.navbar_category (nullable FK → NavbarCategory).
3.  Add Product.nav_category (nullable temp FK → NavbarCategory).
4.  Data migration:
      a. Create one NavbarCategory per top-level Category (copy fields).
      b. Set Category.navbar_category for every child Category.
      c. Set Product.nav_category from the corresponding NavbarCategory slug.
5.  Remove Product.category (old FK → Category) so top-level Category rows
    are no longer referenced by Products.
6.  Remove Category.parent (self-referential FK) to allow safe deletion of
    top-level Category rows without cascade.
7.  Delete top-level Category rows (navbar_category is still null on them).
8.  Make Category.navbar_category NOT NULL.
9.  Rename Product.nav_category → Product.category.
10. Make Product.category NOT NULL with PROTECT.
"""

from django.db import migrations, models, transaction
import django.db.models.deletion


# ── Data migration helpers ────────────────────────────────────────────────────

def migrate_to_navbar_categories(apps, schema_editor):
    Category = apps.get_model('products', 'Category')
    NavbarCategory = apps.get_model('products', 'NavbarCategory')
    Product = apps.get_model('products', 'Product')

    with transaction.atomic():
        # a) Create NavbarCategory from each top-level Category (parent=null)
        slug_to_navbar = {}
        for cat in Category.objects.filter(parent__isnull=True):
            nav_cat = NavbarCategory.objects.create(
                name=cat.name,
                slug=cat.slug,
                description=cat.description,
                image=cat.image.name if cat.image else '',
                order=cat.order,
                is_active=cat.is_active,
            )
            slug_to_navbar[cat.slug] = nav_cat

        # b) Set navbar_category on every child Category
        for cat in Category.objects.filter(parent__isnull=False).select_related('parent'):
            nav_cat = slug_to_navbar.get(cat.parent.slug)
            if nav_cat:
                cat.navbar_category = nav_cat
                cat.save(update_fields=['navbar_category'])

        # c) Set nav_category on each Product from the old category FK
        for product in Product.objects.select_related('category'):
            nav_cat = slug_to_navbar.get(product.category.slug)
            if nav_cat:
                product.nav_category = nav_cat
                product.save(update_fields=['nav_category'])


def delete_toplevel_categories(apps, schema_editor):
    """Delete old top-level Category rows (navbar_category is null on them)."""
    Category = apps.get_model('products', 'Category')
    with transaction.atomic():
        Category.objects.filter(navbar_category__isnull=True).delete()


def reverse_migrate(apps, schema_editor):
    """Reverse: remove all NavbarCategory records (best-effort)."""
    NavbarCategory = apps.get_model('products', 'NavbarCategory')
    NavbarCategory.objects.all().delete()


# ── Migration class ───────────────────────────────────────────────────────────

class Migration(migrations.Migration):
    # Disable global atomic wrapper so each operation runs in its own context;
    # required on PostgreSQL for DDL mixed with DML, safe on SQLite.
    atomic = False

    dependencies = [
        ('products', '0006_product_stock'),
    ]

    operations = [
        # ── Step 1: Create NavbarCategory table ──────────────────────────────
        migrations.CreateModel(
            name='NavbarCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True, help_text='Category description for the frontend')),
                ('image', models.ImageField(blank=True, null=True, upload_to='navbar_categories/')),
                ('order', models.PositiveIntegerField(default=0, help_text='Display order in navigation')),
                ('is_active', models.BooleanField(default=True, help_text='Whether this category is visible on the site')),
            ],
            options={
                'verbose_name': 'Navbar Category',
                'verbose_name_plural': 'Navbar Categories',
                'ordering': ['order', 'name'],
            },
        ),

        # ── Step 2: Add nullable navbar_category FK to Category ───────────────
        migrations.AddField(
            model_name='category',
            name='navbar_category',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subcategories',
                to='products.navbarcategory',
                help_text='The navbar (main) category this subcategory belongs to',
            ),
        ),

        # ── Step 3: Add nullable nav_category temp field to Product ───────────
        migrations.AddField(
            model_name='product',
            name='nav_category',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products_temp',
                to='products.navbarcategory',
            ),
        ),

        # ── Step 4: Data migration ────────────────────────────────────────────
        migrations.RunPython(migrate_to_navbar_categories, reverse_migrate),

        # ── Step 5: Remove old Product.category FK (pointed to Category) ──────
        migrations.RemoveField(
            model_name='product',
            name='category',
        ),

        # ── Step 6: Remove self-referential Category.parent FK ────────────────
        migrations.RemoveField(
            model_name='category',
            name='parent',
        ),

        # ── Step 7: Delete top-level Category rows (navbar_category still null)
        migrations.RunPython(delete_toplevel_categories, migrations.RunPython.noop),

        # ── Step 8: Make Category.navbar_category NOT NULL ────────────────────
        migrations.AlterField(
            model_name='category',
            name='navbar_category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subcategories',
                to='products.navbarcategory',
                help_text='The navbar (main) category this subcategory belongs to',
            ),
        ),

        # ── Step 9: Rename Product.nav_category → Product.category ───────────
        migrations.RenameField(
            model_name='product',
            old_name='nav_category',
            new_name='category',
        ),

        # ── Step 10: Make Product.category NOT NULL with final constraints ─────
        migrations.AlterField(
            model_name='product',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products',
                to='products.navbarcategory',
                help_text='Main navbar category (e.g., Gadgets, Accessories)',
            ),
        ),
    ]
