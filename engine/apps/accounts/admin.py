from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User, SuperUser, StoreUser
from engine.apps.billing.admin import billing_user_actions


# ---------------------------------------------------------------------------
# Shared base configuration
# ---------------------------------------------------------------------------

_common_fieldsets = (
    (None, {"fields": ("email", "password")}),
    (_("Personal info"), {"fields": ("first_name", "last_name", "phone", "avatar")}),
    (
        _("Permissions"),
        {
            "fields": (
                "is_active",
                "is_verified",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        },
    ),
    (_("IDs & timestamps"), {"fields": ("public_id", "date_joined", "updated_at", "last_login")}),
)

_add_fieldsets = (
    (
        None,
        {
            "classes": ("wide",),
            "fields": ("email", "first_name", "last_name", "password1", "password2"),
        },
    ),
)


class BaseUserAdmin(BaseUserAdmin):
    ordering = ["-date_joined"]
    search_fields = ["email", "first_name", "last_name", "public_id"]
    readonly_fields = ["public_id", "date_joined", "updated_at", "last_login"]
    fieldsets = _common_fieldsets
    add_fieldsets = _add_fieldsets
    USERNAME_FIELD = "email"


# ---------------------------------------------------------------------------
# Superusers section — admin accounts (you)
# ---------------------------------------------------------------------------

@admin.register(SuperUser)
class SuperUserAdmin(BaseUserAdmin):
    list_display = ["email", "public_id", "full_name", "is_active", "date_joined"]
    list_filter = ["is_active"]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_superuser=True)

    def save_model(self, request, obj, form, change):
        obj.is_staff = True
        obj.is_superuser = True
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------------------------
# Users section — store owner / staff accounts
# ---------------------------------------------------------------------------

@admin.register(StoreUser)
class StoreUserAdmin(BaseUserAdmin):
    list_display = [
        "email", "public_id", "full_name", "is_verified",
        "is_active", "store_count", "date_joined",
    ]
    list_filter = ["is_active", "is_verified"]
    actions = billing_user_actions

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_superuser=False)

    @admin.display(description="Stores")
    def store_count(self, obj):
        return obj.store_memberships.filter(is_active=True).count()

    def save_model(self, request, obj, form, change):
        obj.is_staff = False
        obj.is_superuser = False
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------------------------
# Unregister the base User from admin — the two proxy sections replace it
# ---------------------------------------------------------------------------

# User is not registered directly; SuperUser and StoreUser cover all accounts.
