from django.contrib import admin
from .models import DashboardBranding


@admin.register(DashboardBranding)
class DashboardBrandingAdmin(admin.ModelAdmin):
    list_display = ("admin_name", "admin_subtitle", "logo")
