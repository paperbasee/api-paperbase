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
        and getattr(user, "is_staff", False)
        and override_flag
        and allowlist_match
    )


class IsPlatformRequest(BasePermission):
    """Allow only platform-host requests (no tenant store derived from host)."""

    def has_permission(self, request, view):
        return bool(getattr(request, "is_platform_request", False))


class IsStaffUser(BasePermission):
    """Backwards-compatible permission for authenticated Django staff users."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsPlatformSuperuser(BasePermission):
    """Allow only authenticated platform superusers."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_superuser
        )


class IsVerifiedUser(BasePermission):
    """Allow only authenticated users with verified email."""

    message = "Email verification is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_verified", False)
        )


class IsDashboardUser(BasePermission):
    """Allow authenticated, verified users in an active store context."""

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
        if request.user.is_staff:
            return True
        from engine.apps.billing.feature_gate import _get_effective_plan

        return _get_effective_plan(request.user) is not None


class IsAdminUser(IsDashboardUser):
    """Alias permission for explicit admin/dashboard checks."""


class IsStorefrontAPIKey(BasePermission):
    """Allow only requests authenticated by active storefront API key."""

    message = "A valid storefront API key is required."

    def has_permission(self, request, view):
        api_key = getattr(request, "api_key", None)
        if not (api_key and getattr(request, "store", None)):
            return False
        return getattr(api_key, "key_type", None) == api_key.KeyType.PUBLIC


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
    """Store-aware permission for dashboard endpoints."""

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "is_verified", False):
            return False
        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return False
        return ctx.membership.role in {
            ctx.membership.Role.OWNER,
            ctx.membership.Role.ADMIN,
            ctx.membership.Role.STAFF,
        }


class IsStoreAdmin(BasePermission):
    """Stricter permission for store administration operations."""

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        if not getattr(user, "is_verified", False):
            return False
        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return False
        return ctx.membership.role in {
            ctx.membership.Role.OWNER,
            ctx.membership.Role.ADMIN,
        }

__all__ = [
    "can_enable_internal_override",
    "IsPlatformRequest",
    "IsStaffUser",
    "IsPlatformSuperuser",
    "IsVerifiedUser",
    "IsDashboardUser",
    "IsAdminUser",
    "IsStorefrontAPIKey",
    "DenyAPIKeyAccess",
    "IsStoreStaff",
    "IsStoreAdmin",
]
