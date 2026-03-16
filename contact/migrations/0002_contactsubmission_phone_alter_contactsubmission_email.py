# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contact', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contactsubmission',
            name='phone',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='contactsubmission',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
    ]
