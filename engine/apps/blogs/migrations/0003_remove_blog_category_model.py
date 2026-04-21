from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("blogs", "0002_remove_blog_status_and_scheduled_at"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="blog",
            name="category",
        ),
        migrations.DeleteModel(
            name="BlogCategory",
        ),
    ]
