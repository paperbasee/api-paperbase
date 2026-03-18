"""
Feature gate service. All feature/limit checks MUST go through these functions.

Resolves: user -> subscription -> plan -> features JSON.
Fallback when no subscription: use default plan (is_default=True).
"""

from rest_framework.exceptions import PermissionDenied

from .models import Plan
from .services import get_active_subscription


def _get_effective_plan(user):
    """
    Return the plan effective for the user: from active subscription, or default plan.
    Returns None if no subscription and no default plan.
    """
    subscription = get_active_subscription(user)
    if subscription:
        return subscription.plan
    return Plan.objects.filter(is_default=True, is_active=True).first()


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
    plan = _get_effective_plan(user)
    return _get_plan_features(plan)


def has_feature(user, feature_key):
    """
    Check if the user has access to the given feature.

    Returns False if no subscription and no default plan, or if feature is missing/false.
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
