from rest_framework.permissions import BasePermission

from engine.core.tenancy import get_active_store


class IsPlatformRequest(BasePermission):
    """Allow only platform-host requests (no tenant store derived from host)."""

    def has_permission(self, request, view):
        return bool(getattr(request, "is_platform_request", False))


class IsStaffUser(BasePermission):
    """
    Backwards-compatible permission: any authenticated Django staff.

    Kept for compatibility; prefer the store-aware classes below.
    """

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
    """
    Allow authenticated users who are either staff OR have an active store membership
    AND an active subscription (or a default plan as fallback).

    Staff/superusers bypass the subscription check and retain full access.
    Django admin site still requires `is_staff` separately.
    """

    def has_permission(self, request, view):
        if not request.user or not getattr(request.user, "is_authenticated", False):
            return False
        if not getattr(request.user, "is_verified", False):
            return False
        if request.user.is_staff:
            return True
        ctx = get_active_store(request)
        if not (ctx.store and ctx.membership):
            return False
        # Require an active subscription or a configured default plan.
        # Importing here to avoid circular import at module load time.
        from engine.apps.billing.feature_gate import _get_effective_plan
        return _get_effective_plan(request.user) is not None


class IsStoreStaff(BasePermission):
    """
    Store-aware permission for dashboard endpoints.

    Requires:
    - authenticated user
    - active store resolved
    - active StoreMembership with role in {OWNER, ADMIN, STAFF}
    """

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
    """
    Stricter permission for store administration operations.

    Requires role in {OWNER, ADMIN}.
    """

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

