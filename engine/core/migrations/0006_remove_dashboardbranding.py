# Remove DashboardBranding - branding now lives on Store model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_alter_dashboardbranding_admin_name_and_more'),
    ]

    operations = [
        migrations.DeleteModel(name='DashboardBranding'),
    ]
