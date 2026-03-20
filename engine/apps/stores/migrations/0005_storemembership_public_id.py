from django.db import migrations, models
import uuid


def generate_membership_public_ids(apps, schema_editor):
    StoreMembership = apps.get_model('stores', 'StoreMembership')
    for membership in StoreMembership.objects.filter(public_id=''):
        membership.public_id = f"mbr_{uuid.uuid4().hex[:20]}"
        membership.save(update_fields=['public_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('stores', '0004_storedeletionjob_public_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='storemembership',
            name='public_id',
            field=models.CharField(
                db_index=True,
                default='',
                editable=False,
                help_text='Non-sequential public identifier used in APIs and URLs (e.g. mbr_xxx).',
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(generate_membership_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='storemembership',
            name='public_id',
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text='Non-sequential public identifier used in APIs and URLs (e.g. mbr_xxx).',
                max_length=32,
                unique=True,
            ),
        ),
    ]
