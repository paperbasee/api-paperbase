from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0002_remove_product_badge_and_is_featured"),
    ]

    operations = [
        migrations.RunSQL(
            "DROP TABLE IF EXISTS reviews_review;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            "DELETE FROM django_migrations WHERE app = 'reviews';",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
