"""
Feature gate service. All feature/limit checks MUST go through these functions.

Resolves: user -> active subscription -> plan -> features JSON.
No fallback: users without an active subscription get empty features/limits.

Results are cached per-user to avoid repeated subscription/plan lookups.
"""

from django.conf import settings
from rest_framework.exceptions import PermissionDenied

from engine.core import cache_service


def _get_effective_plan(user):
    """
    Return the plan from the user's subscription for dashboard feature UI.

    ACTIVE/GRACE: paid access row. EXPIRED: last candidate row so dashboard limits/features
    match the lapsed plan (storefront APIs are blocked separately).
    """
    # Imported lazily to avoid circular import: billing.services -> emails.triggers -> feature_gate.
    from .services import get_active_subscription
    from .subscription_status import get_candidate_subscription_row, get_user_subscription_status

    subscription = get_active_subscription(user)
    if subscription:
        return subscription.plan
    if get_user_subscription_status(user) == "EXPIRED":
        sub = get_candidate_subscription_row(user)
        if sub:
            return sub.plan
    return None


def _get_plan_features(plan):
    """Extract {limits, features} from plan.features. Returns normalized dict."""
    if not plan or not plan.features:
        return {"limits": {}, "features": {}}
    data = plan.features
    limits = data.get("limits")
    features = data.get("features")
    return {
        "limits": dict(limits) if isinstance(limits, dict) else {},
        "features": dict(features) if isinstance(features, dict) else {},
    }


def get_feature_config(user):
    """
    Return the feature config for the user's effective plan.

    Returns:
        {"features": {...}, "limits": {...}}
        Empty dicts if no plan.
    """
    public_id = getattr(user, "public_id", None)
    if not public_id:
        plan = _get_effective_plan(user)
        return _get_plan_features(plan)

    key = cache_service.build_user_key(public_id, "feature_config")
    ttl = getattr(settings, "CACHE_TTL_FEATURE_CONFIG", 600)

    def fetcher():
        plan = _get_effective_plan(user)
        return _get_plan_features(plan)

    return cache_service.get_or_set(key, fetcher, ttl)


def has_feature(user, feature_key):
    """
    Check if the user has access to the given feature.

    Returns False if no active subscription or if the feature is missing/false.
    """
    config = get_feature_config(user)
    return config["features"].get(feature_key, False) is True


def get_limit(user, limit_key):
    """
    Get the limit value for the user (e.g. max_stores).

    Returns 0 if no plan or limit missing.
    """
    config = get_feature_config(user)
    return config["limits"].get(limit_key, 0)


def require_feature(user, feature_key):
    """
    Raise PermissionDenied if the user does not have the feature.
    """
    if not has_feature(user, feature_key):
        raise PermissionDenied(
            detail=f"This feature ({feature_key}) is not available on your plan. Please upgrade."
        )


def invalidate_feature_config_cache(user) -> None:
    """Clear cached feature config for a user (call on subscription changes)."""
    public_id = getattr(user, "public_id", None)
    if public_id:
        cache_service.invalidate_user_resource(public_id, "feature_config")
