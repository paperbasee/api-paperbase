from django.conf import settings
from rest_framework.permissions import BasePermission

from engine.core.tenancy import get_active_store


def can_enable_internal_override(*, user, client_ip: str) -> bool:
    allowlist = set(getattr(settings, "INTERNAL_OVERRIDE_IP_ALLOWLIST", []) or [])
    allowlist_match = bool(client_ip) and client_ip in allowlist
    override_flag = bool(getattr(settings, "SECURITY_INTERNAL_OVERRIDE_ALLOWED", False))
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
        and override_flag
        and allowlist_match
    )


class IsPlatformRequest(BasePermission):
    """Allow only platform-host requests (no tenant store derived from host)."""

    def has_permission(self, request, view):
        return bool(getattr(request, "is_platform_request", False))


class IsPlatformSuperuser(BasePermission):
    """Allow only authenticated platform superusers."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_superuser
        )


class IsPlatformSuperuserOrStoreAdmin(BasePermission):
    """Platform superuser or store OWNER/ADMIN (same gate as IsStoreAdmin, with superuser bypass)."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
            return True
        return IsStoreAdmin().has_permission(request, view)


class IsVerifiedUser(BasePermission):
    """Allow only authenticated users with verified email."""

    message = "Email verification is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_verified", False)
        )


class IsSubscribedUser(BasePermission):
    """Allow only authenticated users with an active subscription."""

    message = "An active subscription is required."

    def has_permission(self, request, view):
        if not request.user or not getattr(request.user, "is_authenticated", False):
            return False
        if getattr(request.user, "is_superuser", False):
            return True
        from engine.apps.billing.subscription_status import dashboard_subscription_access_ok

        return dashboard_subscription_access_ok(request.user)


class IsDashboardUser(BasePermission):
    """Allow authenticated, verified, subscribed users in an active store context."""

    def has_permission(self, request, view):
        if not request.user or not getattr(request.user, "is_authenticated", False):
            return False
        if getattr(request.user, "is_superuser", False):
            return True
        if not getattr(request.user, "is_verified", False):
            return False
        ctx = get_active_store(request)
        if not (ctx.store and ctx.membership):
            return False
        from engine.apps.billing.subscription_status import dashboard_subscription_access_ok

        return dashboard_subscription_access_ok(request.user)


class IsAdminUser(IsDashboardUser):
    """Alias permission for explicit admin/dashboard checks."""


class IsStorefrontAPIKey(BasePermission):
    """Allow only requests authenticated by active storefront API key."""

    message = "A valid storefront API key is required."

    def has_permission(self, request, view):
        from rest_framework.exceptions import PermissionDenied

        from engine.apps.billing.subscription_status import (
            SUBSCRIPTION_EXPIRED_DETAIL,
            get_user_subscription_status,
        )

        api_key = getattr(request, "api_key", None)
        store = getattr(request, "store", None)
        if not (api_key and store):
            return False
        if getattr(api_key, "key_type", None) != api_key.KeyType.PUBLIC:
            return False
        owner = getattr(store, "owner", None)
        if owner is not None and get_user_subscription_status(owner) == "EXPIRED":
            raise PermissionDenied(detail=SUBSCRIPTION_EXPIRED_DETAIL)
        return True


class DenyAPIKeyAccess(BasePermission):
    """Explicitly block API-key bearer tokens on admin-only endpoints."""

    message = "API key cannot access this endpoint."

    def has_permission(self, request, view):
        if getattr(request, "api_key", None):
            return False
        header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
        parts = header.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if token.startswith("ak_pk_") or token.startswith("ak_sk_"):
            return False
        return True


class IsStoreStaff(BasePermission):
    """Store-aware permission for dashboard endpoints. Requires active subscription."""

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not getattr(user, "is_verified", False):
            return False
        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return False
        if ctx.membership.role not in {
            ctx.membership.Role.OWNER,
            ctx.membership.Role.ADMIN,
            ctx.membership.Role.STAFF,
        }:
            return False
        from engine.apps.billing.subscription_status import dashboard_subscription_access_ok

        return dashboard_subscription_access_ok(user)


class IsStoreAdmin(BasePermission):
    """Stricter permission for store administration. Requires active subscription."""

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not getattr(user, "is_verified", False):
            return False
        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return False
        if ctx.membership.role not in {
            ctx.membership.Role.OWNER,
            ctx.membership.Role.ADMIN,
        }:
            return False
        from engine.apps.billing.subscription_status import dashboard_subscription_access_ok

        return dashboard_subscription_access_ok(user)

__all__ = [
    "can_enable_internal_override",
    "IsPlatformRequest",
    "IsPlatformSuperuser",
    "IsPlatformSuperuserOrStoreAdmin",
    "IsVerifiedUser",
    "IsSubscribedUser",
    "IsDashboardUser",
    "IsAdminUser",
    "IsStorefrontAPIKey",
    "DenyAPIKeyAccess",
    "IsStoreStaff",
    "IsStoreAdmin",
]
