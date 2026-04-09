"""Billing admin. Subscription state changes MUST go through service layer (admin actions)."""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.utils.html import format_html

from .models import Payment, Plan, Subscription
from .services import activate_subscription, extend_subscription
from engine.apps.stores.services import sync_order_email_notification_settings_for_user

User = get_user_model()
PLAN_ESSENTIAL = "Essential"
PLAN_PREMIUM = "Premium"


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
    search_fields = ("user__email",)
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


@admin.action(description="Approve pending payment and activate subscription")
def approve_pending_payment_action(modeladmin, request, queryset):
    """Approve selected PENDING payments, activating a subscription for each user."""
    success, skipped = 0, 0
    for payment in queryset.select_related("user", "plan"):
        if payment.status != Payment.Status.PENDING:
            modeladmin.message_user(
                request,
                f"Payment #{payment.id} ({payment.user}) is not pending — skipped.",
                messages.WARNING,
            )
            skipped += 1
            continue
        if not payment.plan:
            modeladmin.message_user(
                request,
                f"Payment #{payment.id} ({payment.user}) has no plan linked — skipped.",
                messages.ERROR,
            )
            skipped += 1
            continue
        try:
            activate_subscription(
                user=payment.user,
                plan=payment.plan,
                billing_cycle=payment.plan.billing_cycle,
                duration_days=30 if payment.plan.billing_cycle == "monthly" else 365,
                source="payment",
                amount=payment.amount,
                provider=payment.provider,
                change_reason="Admin approved pending payment",
                existing_pending_payment=payment,
            )
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for {payment.user}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(
            request,
            f"Approved {success} payment(s) and activated subscription(s).",
            messages.SUCCESS,
        )
    if skipped:
        modeladmin.message_user(request, f"{skipped} payment(s) skipped.", messages.WARNING)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "currency",
        "status",
        "provider",
        "plan",
        "transaction_id",
        "subscription",
        "created_at",
    )
    list_filter = ("status", "provider")
    search_fields = ("user__email", "transaction_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    list_select_related = ("user", "subscription", "plan")
    raw_id_fields = ("user", "subscription", "plan")
    actions = [approve_pending_payment_action]


# ---------------------------------------------------------------------------
# Admin Actions (call service layer)
# ---------------------------------------------------------------------------


def _get_plan(name):
    plan = Plan.objects.filter(name__iexact=name, is_active=True).first()
    return plan


def _activate_plan_for_users(modeladmin, request, queryset, plan_name, duration_days, source, label):
    plan = _get_plan(plan_name)
    if not plan:
        modeladmin.message_user(
            request,
            f'"{plan_name}" plan not found or inactive. Create it first.',
            messages.ERROR,
        )
        return
    success = 0
    change_reason = f"Admin action: {label}"
    for user in queryset:
        try:
            activate_subscription(
                user=user,
                plan=plan,
                billing_cycle="monthly",
                duration_days=duration_days,
                source=source,
                amount=0,
                provider="manual",
                change_reason=change_reason,
            )
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for {user}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(request, f"{label} for {success} user(s).", messages.SUCCESS)


@admin.action(description="Grant Essential plan (30 days)")
def grant_basic_action(modeladmin, request, queryset):
    _activate_plan_for_users(
        modeladmin, request, queryset,
        plan_name=PLAN_ESSENTIAL,
        duration_days=30,
        source="manual",
        label="Essential plan activated",
    )


@admin.action(description="Grant Premium plan (30 days)")
def activate_premium_action(modeladmin, request, queryset):
    _activate_plan_for_users(
        modeladmin, request, queryset,
        plan_name=PLAN_PREMIUM,
        duration_days=30,
        source="manual",
        label="Premium plan activated",
    )


@admin.action(description="Grant Free Trial — Premium (14 days)")
def grant_free_trial_action(modeladmin, request, queryset):
    _activate_plan_for_users(
        modeladmin, request, queryset,
        plan_name=PLAN_PREMIUM,
        duration_days=14,
        source="trial",
        label="14-day Premium trial granted",
    )


@admin.action(description="Extend current subscription by 30 days")
def extend_30_days_action(modeladmin, request, queryset):
    from .services import get_active_subscription
    success, skipped = 0, 0
    for user in queryset:
        sub = get_active_subscription(user)
        if not sub:
            skipped += 1
            continue
        try:
            extend_subscription(sub, days=30)
            success += 1
        except Exception as e:
            modeladmin.message_user(request, f"Failed for {user}: {e}", messages.ERROR)
    if success:
        modeladmin.message_user(request, f"Extended subscription by 30 days for {success} user(s).", messages.SUCCESS)
    if skipped:
        modeladmin.message_user(request, f"{skipped} user(s) had no active subscription — skipped.", messages.WARNING)


@admin.action(description="Revoke current plan (cancel subscription)")
def revoke_plan_action(modeladmin, request, queryset):
    from django.utils import timezone
    from .feature_gate import invalidate_feature_config_cache

    success, skipped = 0, 0
    for user in queryset:
        updated = Subscription.objects.filter(
            user=user,
            status=Subscription.Status.ACTIVE,
        ).update(status=Subscription.Status.CANCELED, updated_at=timezone.now())
        if updated:
            invalidate_feature_config_cache(user)
            sync_order_email_notification_settings_for_user(user)
            success += 1
        else:
            skipped += 1
    if success:
        modeladmin.message_user(request, f"Revoked active subscription for {success} user(s).", messages.SUCCESS)
    if skipped:
        modeladmin.message_user(request, f"{skipped} user(s) had no active subscription — skipped.", messages.WARNING)


# ---------------------------------------------------------------------------
# Extend action on Subscription list
# ---------------------------------------------------------------------------

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


SubscriptionAdmin.actions = [extend_subscription_action]

# Exported to engine.apps.accounts.admin — attached to the Users section
billing_user_actions = [
    grant_basic_action,
    activate_premium_action,
    grant_free_trial_action,
    extend_30_days_action,
    revoke_plan_action,
]
