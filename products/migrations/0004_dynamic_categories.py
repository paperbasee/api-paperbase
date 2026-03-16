# Generated migration for dynamic categories

from django.db import migrations, models, transaction
import django.db.models.deletion


def create_initial_categories(apps, schema_editor):
    """Create initial category structure with Gadgets and Accessories."""
    Category = apps.get_model('products', 'Category')
    
    with transaction.atomic():
        # Create main categories
        gadgets = Category.objects.create(
            name='Gadgets',
            slug='gadgets',
            description='Discover the latest in tech innovation with our curated gadgets collection.',
            order=1,
            is_active=True,
        )
        
        accessories = Category.objects.create(
            name='Accessories',
            slug='accessories',
            description='Elevate your tech experience with our premium accessories collection.',
            order=2,
            is_active=True,
        )
        
        # Create Gadgets subcategories
        gadgets_subcats = [
            ('New In', 'new', 1),
            ('Audio', 'audio', 2),
            ('Wearables', 'wearables', 3),
            ('Smart Home', 'smart-home', 4),
            ('Gaming', 'gaming', 5),
            ('Cameras', 'cameras', 6),
            ('Drones', 'drones', 7),
        ]
        
        for name, slug, order in gadgets_subcats:
            Category.objects.create(
                name=name,
                slug=slug,
                parent=gadgets,
                order=order,
                is_active=True,
            )
        
        # Create Accessories subcategories (without Cases & Covers, Screen Protectors, Bags & Sleeves)
        # Adding Power Bank as requested
        accessories_subcats = [
            ('New In', 'accessories-new', 1),  # Use unique slug to avoid conflict with gadgets
            ('Chargers', 'chargers', 2),
            ('Cables', 'cables', 3),
            ('Stands & Mounts', 'stands', 4),
            ('Power Bank', 'power-bank', 5),  # New category as requested
        ]
        
        for name, slug, order in accessories_subcats:
            Category.objects.create(
                name=name,
                slug=slug,
                parent=accessories,
                order=order,
                is_active=True,
            )


def migrate_product_categories(apps, schema_editor):
    """Migrate existing products to use ForeignKey categories."""
    Product = apps.get_model('products', 'Product')
    Category = apps.get_model('products', 'Category')
    
    with transaction.atomic():
        # Build lookup maps
        main_categories = {c.slug: c for c in Category.objects.filter(parent__isnull=True)}
        sub_categories = {c.slug: c for c in Category.objects.filter(parent__isnull=False)}
        
        # Map old sub_category values to new slugs
        old_to_new_subcat = {
            'new': 'new',  # For gadgets
            'audio': 'audio',
            'wearables': 'wearables',
            'smart-home': 'smart-home',
            'gaming': 'gaming',
            'cameras': 'cameras',
            'drones': 'drones',
            'chargers': 'chargers',
            'cables': 'cables',
            'stands': 'stands',
            # Removed categories - products will have sub_category set to null
            'cases': None,
            'screen-protectors': None,
            'bags': None,
        }
        
        for product in Product.objects.all():
            # Get the main category
            old_cat = getattr(product, 'category_old', None) or getattr(product, 'category', None)
            if isinstance(old_cat, str):
                main_cat = main_categories.get(old_cat)
                if main_cat:
                    product.category_new = main_cat
            
            # Get the subcategory
            old_subcat = getattr(product, 'sub_category_old', None) or getattr(product, 'sub_category', None)
            if isinstance(old_subcat, str) and old_subcat:
                new_subcat_slug = old_to_new_subcat.get(old_subcat)
                if new_subcat_slug:
                    # Handle "new" which exists in both gadgets and accessories
                    if new_subcat_slug == 'new':
                        if old_cat == 'accessories':
                            new_subcat_slug = 'accessories-new'
                    sub_cat = sub_categories.get(new_subcat_slug)
                    if sub_cat:
                        product.sub_category_new = sub_cat
            
            product.save()


def reverse_migration(apps, schema_editor):
    """Reverse the category migration."""
    Category = apps.get_model('products', 'Category')
    with transaction.atomic():
        Category.objects.all().delete()


class Migration(migrations.Migration):
    # Disable atomic transactions to avoid PostgreSQL trigger conflicts
    # when performing multiple schema changes on the same table
    atomic = False

    dependencies = [
        ('products', '0003_alter_product_category'),
    ]

    operations = [
        # Step 1: Add new fields to Category model
        migrations.AddField(
            model_name='category',
            name='description',
            field=models.TextField(blank=True, help_text='Category description for the frontend'),
        ),
        migrations.AddField(
            model_name='category',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Whether this category is visible on the site'),
        ),
        
        # Step 2: Rename old category fields
        migrations.RenameField(
            model_name='product',
            old_name='category',
            new_name='category_old',
        ),
        migrations.RenameField(
            model_name='product',
            old_name='sub_category',
            new_name='sub_category_old',
        ),
        
        # Step 3: Add new ForeignKey fields (nullable initially)
        migrations.AddField(
            model_name='product',
            name='category_new',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products_new',
                to='products.category',
                help_text='Main category (e.g., Gadgets, Accessories)',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='sub_category_new',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='subcategory_products_new',
                to='products.category',
                help_text='Subcategory (e.g., Audio, Chargers, Power Bank)',
            ),
        ),
        
        # Step 4: Create initial categories
        migrations.RunPython(create_initial_categories, reverse_migration),
        
        # Step 5: Migrate existing products
        migrations.RunPython(migrate_product_categories, migrations.RunPython.noop),
        
        # Step 6: Remove old fields
        migrations.RemoveField(
            model_name='product',
            name='category_old',
        ),
        migrations.RemoveField(
            model_name='product',
            name='sub_category_old',
        ),
        
        # Step 7: Rename new fields to final names
        migrations.RenameField(
            model_name='product',
            old_name='category_new',
            new_name='category',
        ),
        migrations.RenameField(
            model_name='product',
            old_name='sub_category_new',
            new_name='sub_category',
        ),
        
        # Step 8: Update field constraints - make category required
        migrations.AlterField(
            model_name='product',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products',
                to='products.category',
                help_text='Main category (e.g., Gadgets, Accessories)',
            ),
        ),
        migrations.AlterField(
            model_name='product',
            name='sub_category',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='subcategory_products',
                to='products.category',
                help_text='Subcategory (e.g., Audio, Chargers, Power Bank)',
            ),
        ),
    ]
