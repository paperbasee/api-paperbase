from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import CharField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from engine.utils.time import bd_today
from django.utils.translation import gettext_lazy as _

from .models import User, SuperUser, StoreUser, UserTwoFactor
from engine.apps.billing.admin import billing_user_actions
from engine.apps.billing.models import Plan, Subscription


# ---------------------------------------------------------------------------
# Shared base configuration
# ---------------------------------------------------------------------------

_common_fieldsets = (
    (None, {"fields": ("email", "password")}),
    (_("Personal info"), {"fields": ("first_name", "last_name", "phone", "avatar_seed")}),
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
        "email",
        "public_id",
        "full_name",
        "subscription_plan_display",
        "is_verified",
        "two_factor_enabled",
        "is_active",
        "store_count",
        "date_joined",
    ]
    list_filter = ["is_active", "is_verified"]
    actions = billing_user_actions

    def get_queryset(self, request):
        qs = super().get_queryset(request).filter(is_superuser=False)
        today = bd_today()
        # Display-only: shows default plan name when no active subscription.
        # This does NOT grant access — access requires an active subscription.
        default_plan_name = (
            Plan.objects.filter(is_default=True, is_active=True)
            .values_list("name", flat=True)
            .first()
        ) or "—"
        active_plan_sq = (
            Subscription.objects.filter(
                user_id=OuterRef("pk"),
                status=Subscription.Status.ACTIVE,
                end_date__gte=today,
            )
            .order_by("-created_at")
            .values("plan__name")[:1]
        )
        return qs.annotate(
            _effective_plan_name=Coalesce(
                Subquery(active_plan_sq, output_field=CharField()),
                Value(default_plan_name),
                output_field=CharField(),
            )
        )

    @admin.display(description="Plan", ordering="_effective_plan_name")
    def subscription_plan_display(self, obj):
        return getattr(obj, "_effective_plan_name", "—")

    @admin.display(description="Stores")
    def store_count(self, obj):
        return obj.store_memberships.filter(is_active=True).count()

    @admin.display(description="2FA")
    def two_factor_enabled(self, obj):
        profile = getattr(obj, "two_factor_profile", None)
        return bool(profile and profile.is_enabled)

    def save_model(self, request, obj, form, change):
        obj.is_staff = False
        obj.is_superuser = False
        super().save_model(request, obj, form, change)

        if change and "email" in form.changed_data:
            self._sync_email_to_owned_stores(obj)

    @staticmethod
    def _sync_email_to_owned_stores(user):
        """Propagate User.email to Store.owner_email for every store the user owns."""
        from engine.apps.stores.models import StoreMembership

        owned_store_ids = StoreMembership.objects.filter(
            user=user,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        ).values_list("store_id", flat=True)

        if owned_store_ids:
            from engine.apps.stores.models import Store

            Store.objects.filter(id__in=owned_store_ids).update(
                owner_email=user.email,
            )


# ---------------------------------------------------------------------------
# Unregister the base User from admin — the two proxy sections replace it
# ---------------------------------------------------------------------------

# User is not registered directly; SuperUser and StoreUser cover all accounts.


@admin.register(UserTwoFactor)
class UserTwoFactorAdmin(admin.ModelAdmin):
    list_display = ["user", "is_enabled", "is_locked_view", "updated_at"]
    search_fields = ["user__email", "user__public_id"]
    list_filter = ["is_enabled"]
    readonly_fields = ["created_at", "updated_at", "last_used_step"]
    actions = ["admin_disable_2fa"]

    @admin.display(description="Locked")
    def is_locked_view(self, obj):
        return obj.is_locked()

    @admin.action(description="Disable selected user 2FA")
    def admin_disable_2fa(self, request, queryset):
        queryset.update(
            is_enabled=False,
            secret_encrypted="",
            pending_secret_encrypted="",
            failed_attempts=0,
            locked_until=None,
            last_used_step=None,
        )
