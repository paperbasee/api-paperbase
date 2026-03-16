import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wishlist', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='wishlistitem',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='wishlist_items',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='wishlistitem',
            name='session_key',
            field=models.CharField(blank=True, db_index=True, default='', max_length=40),
            preserve_default=False,
        ),
        migrations.AlterUniqueTogether(
            name='wishlistitem',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='wishlistitem',
            constraint=models.UniqueConstraint(
                condition=models.Q(('user__isnull', False)),
                fields=('user', 'product'),
                name='unique_user_wishlist_item',
            ),
        ),
        migrations.AddConstraint(
            model_name='wishlistitem',
            constraint=models.UniqueConstraint(
                condition=models.Q(('user__isnull', True)),
                fields=('session_key', 'product'),
                name='unique_session_wishlist_item',
            ),
        ),
    ]
