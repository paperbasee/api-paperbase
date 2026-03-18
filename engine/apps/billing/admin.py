"""Billing admin. Subscription state changes MUST go through service layer (admin actions)."""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib import messages
from django.utils.html import format_html

from .models import Payment, Plan, Subscription
from .services import activate_subscription, extend_subscription

User = get_user_model()


# ---------------------------------------------------------------------------
# Plan Admin
# ---------------------------------------------------------------------------


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "price", "billing_cycle", "max_stores_display", "is_default", "is_active", "created_at")
    list_filter = ("is_active", "billing_cycle", "is_default")
    search_fields = ("name",)
    ordering = ("price",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "price", "billing_cycle", "is_default", "is_active")}),
        ("Features", {"fields": ("features",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Max stores")
    def max_stores_display(self, obj):
        limits = (obj.features or {}).get("limits") or {}
        return limits.get("max_stores", "-")


# ---------------------------------------------------------------------------
# Subscription Admin (read-only for state; use actions to change)
# ---------------------------------------------------------------------------


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "plan",
        "status",
        "source",
        "start_date",
        "end_date",
        "days_remaining_display",
        "created_at",
    )
    list_filter = ("status", "plan", "source")
    search_fields = ("user__username", "user__email")
    ordering = ("-created_at",)
    readonly_fields = (
        "user",
        "plan",
        "status",
        "billing_cycle",
        "start_date",
        "end_date",
        "source",
        "created_at",
        "updated_at",
    )
    list_select_related = ("user", "plan")

    def has_add_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

    @admin.display(description="Days remaining")
    def days_remaining_display(self, obj):
        days = obj.days_remaining()
        if days < 0:
            return format_html('<span style="color: #999;">Expired</span>')
        if days <= 7:
            return format_html('<span style="color: #c00;">{}</span>', days)
        return days


# ---------------------------------------------------------------------------
# Payment Admin
# ---------------------------------------------------------------------------


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "currency",
        "status",
        "provider",
        "subscription",
        "created_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("user__username", "user__email", "transaction_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    list_select_related = ("user", "subscription")
    raw_id_fields = ("user", "subscription")


# ---------------------------------------------------------------------------
# Admin Actions (call service layer)
# ---------------------------------------------------------------------------


def _get_premium_plan():
    return Plan.objects.filter(name="premium", is_active=True).first()


@admin.action(description="Activate Premium (manual)")
def activate_premium_action(modeladmin, request, queryset):
    plan = _get_premium_plan()
    if not plan:
        modeladmin.message_user(request, "Premium plan not found. Create it first.", messages.ERROR)
        return
    success = 0
    for user in queryset:
        try:
            activate_subscription(
                user=user,
                plan=plan,
                billing_cycle="monthly",
                duration_days=30,
                source="manual",
                amount=0,
                provider="manual",
            )
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for {user}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(request, f"Activated Premium for {success} user(s).", messages.SUCCESS)


@admin.action(description="Grant Free Trial (14 days)")
def grant_free_trial_action(modeladmin, request, queryset):
    plan = _get_premium_plan()
    if not plan:
        modeladmin.message_user(request, "Premium plan not found. Create it first.", messages.ERROR)
        return
    success = 0
    for user in queryset:
        try:
            activate_subscription(
                user=user,
                plan=plan,
                billing_cycle="monthly",
                duration_days=14,
                source="trial",
                amount=0,
                provider="manual",
            )
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for {user}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(request, f"Granted 14-day trial for {success} user(s).", messages.SUCCESS)


@admin.action(description="Extend subscription by 30 days")
def extend_subscription_action(modeladmin, request, queryset):
    success = 0
    for sub in queryset:
        try:
            extend_subscription(sub, days=30)
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for subscription {sub.id}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(request, f"Extended {success} subscription(s) by 30 days.", messages.SUCCESS)


# Add extend action to Subscription admin
SubscriptionAdmin.actions = [extend_subscription_action]

# Custom User Admin with billing actions
admin.site.unregister(User)


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    actions = [activate_premium_action, grant_free_trial_action]
