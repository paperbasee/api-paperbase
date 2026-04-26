from datetime import timedelta
from pathlib import Path
import os
import sys
import datetime
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default[:] if default else []
    return [item.strip() for item in value.split(",") if item.strip()]


def env_crontab(name: str, default: str):
    value = (os.getenv(name, default) or default).strip()
    parts = value.split()
    if len(parts) != 5:
        parts = default.split()
    minute, hour, day_of_month, month_of_year, day_of_week = parts
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


# Used to make test runs deterministic (e.g. avoid DRF throttling interfering with auth tests).
TESTING = (
    any(arg == "test" or arg.startswith("test") for arg in sys.argv)
    or any("pytest" in (arg or "") for arg in sys.argv)
    or "PYTEST_CURRENT_TEST" in os.environ
)

# Applications
INSTALLED_APPS = [
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "engine.core",
    "engine.apps.stores.apps.StoresConfig",
    "engine.apps.billing",
    "engine.apps.products.apps.ProductsConfig",
    "engine.apps.orders.apps.OrdersConfig",
    "engine.apps.notifications",
    "engine.apps.support.apps.SupportConfig",
    "engine.apps.accounts",
    "engine.apps.backup.apps.BackupConfig",
    "engine.apps.customers.apps.CustomersConfig",
    "engine.apps.inventory",
    "engine.apps.shipping",
    "engine.apps.banners",
    "engine.apps.blogs",
    "engine.apps.basic_analytics",
    "engine.apps.couriers",
    "engine.apps.fraud_check",
    "engine.apps.marketing_integrations",
    "engine.apps.tracking",
    "engine.apps.emails",
]

ASGI_APPLICATION = "config.asgi.application"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Tenant API auth
TENANT_API_PREFIX = "/api/v1/"
TENANT_API_KEY_ENFORCE = env_bool("TENANT_API_KEY_ENFORCE", True)
STORE_API_KEY_SECRET = os.getenv("STORE_API_KEY_SECRET", "").strip()

# Manual payment receiver numbers (bKash / Nagad).
# Set via environment variables in production.
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "").strip()
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "").strip()
STORE_API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS = int(
    os.getenv("STORE_API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS", "60")
)
STORE_ACTIVITY_TOUCH_INTERVAL_SECONDS = int(
    os.getenv("STORE_ACTIVITY_TOUCH_INTERVAL_SECONDS", "60")
)
# CAPI buffer config (can be overridden per environment)
CAPI_BATCH_SIZE = int(os.getenv("CAPI_BATCH_SIZE", "500"))
CAPI_MAX_STREAM_LEN = int(os.getenv("CAPI_MAX_STREAM_LEN", "5000"))
CAPI_FLUSH_INTERVAL_SECONDS = float(os.getenv("CAPI_FLUSH_INTERVAL_SECONDS", "10.0"))
CAPI_EARLY_FLUSH_THRESHOLD = int(os.getenv("CAPI_EARLY_FLUSH_THRESHOLD", "500"))
STORE_OTP_RATE_LIMIT_CACHE_ALIAS = os.getenv("STORE_OTP_RATE_LIMIT_CACHE_ALIAS", "default")
INTERNAL_OVERRIDE_IP_ALLOWLIST = env_list(
    "INTERNAL_OVERRIDE_IP_ALLOWLIST",
    default=["127.0.0.1", "::1"],
)
SECURITY_INTERNAL_OVERRIDE_ALLOWED = env_bool("SECURITY_INTERNAL_OVERRIDE_ALLOWED", False)
TENANT_GUARD_STRICT_DEV = env_bool("TENANT_GUARD_STRICT_DEV", env_bool("CI", False))

# Client IP: Django request.META key (e.g. HTTP_X_FORWARDED_FOR). Must match trusted ingress.
TRUSTED_IP_HEADER = (os.getenv("TRUSTED_IP_HEADER", "HTTP_X_FORWARDED_FOR") or "HTTP_X_FORWARDED_FOR").strip()

# Cloudflare Turnstile (login/register). Server-side secret only; leave unset to skip checks locally.
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
# When true, skip siteverify even if TURNSTILE_SECRET_KEY is set (local dev only; never enable in production).
TURNSTILE_SKIP_VERIFICATION = env_bool("TURNSTILE_SKIP_VERIFICATION", False)

# Fraud check (BDCourier courier-check)
FRAUD_API_KEY = os.getenv("FRAUD_API_KEY", "").strip()
STORE_DAILY_LIMIT = int(os.getenv("STORE_DAILY_LIMIT", "0"))
STORE_MONTHLY_LIMIT = int(os.getenv("STORE_MONTHLY_LIMIT", "0"))
GLOBAL_DAILY_LIMIT = int(os.getenv("GLOBAL_DAILY_LIMIT", "0"))
GLOBAL_MONTHLY_LIMIT = int(os.getenv("GLOBAL_MONTHLY_LIMIT", "0"))
FRAUD_CACHE_TTL_DAYS = int(os.getenv("FRAUD_CACHE_TTL_DAYS", "3"))

# ---------------------------------------------------------------------------
# Pre-backup / steady-state prune of non-critical tables (physical backups
# cannot exclude tables; see docs/backup-restore.md).
# ---------------------------------------------------------------------------
BACKUP_PRUNE_ENABLED = env_bool("BACKUP_PRUNE_ENABLED", True)
BACKUP_PRUNE_BATCH_SIZE = max(50, int(os.getenv("BACKUP_PRUNE_BATCH_SIZE", "500")))
BACKUP_PRUNE_EMAIL_LOG_DAYS = int(os.getenv("BACKUP_PRUNE_EMAIL_LOG_DAYS", "90"))
BACKUP_PRUNE_ACTIVITY_LOG_DAYS = int(os.getenv("BACKUP_PRUNE_ACTIVITY_LOG_DAYS", "180"))
BACKUP_PRUNE_ADMIN_LOG_DAYS = int(os.getenv("BACKUP_PRUNE_ADMIN_LOG_DAYS", "180"))
BACKUP_PRUNE_FRAUD_CHECK_LOG_DAYS = int(os.getenv("BACKUP_PRUNE_FRAUD_CHECK_LOG_DAYS", "30"))
BACKUP_PRUNE_NOTIFICATION_DISMISSAL_DAYS = int(
    os.getenv("BACKUP_PRUNE_NOTIFICATION_DISMISSAL_DAYS", "90")
)
BACKUP_PRUNE_DASHBOARD_SNAPSHOT_DAYS = int(
    os.getenv("BACKUP_PRUNE_DASHBOARD_SNAPSHOT_DAYS", "400")
)
BACKUP_PRUNE_STORE_EVENT_LOG_HOURS = int(os.getenv("BACKUP_PRUNE_STORE_EVENT_LOG_HOURS", "72"))
BACKUP_PRUNE_ORDER_EXPORT_JOB_DAYS = int(os.getenv("BACKUP_PRUNE_ORDER_EXPORT_JOB_DAYS", "30"))

# Storefront rate limits (per minute, fixed window) — see engine.core.rate_limit
TENANT_STOREFRONT_RATE_LIMIT_PER_IP_PER_MIN = int(
    os.getenv("TENANT_STOREFRONT_RATE_LIMIT_PER_IP_PER_MIN", "100")
)
TENANT_API_KEY_AGGREGATE_RATE_LIMIT_PER_MIN = int(
    os.getenv("TENANT_API_KEY_AGGREGATE_RATE_LIMIT_PER_MIN", "5000")
)

# ---------------------------------------------------------------------------
# Data cache TTLs (seconds) — used by engine.core.cache_service
# ---------------------------------------------------------------------------
CACHE_TTL_PRODUCT_LIST = int(os.getenv("CACHE_TTL_PRODUCT_LIST", "120"))
CACHE_TTL_PRODUCT_DETAIL = int(os.getenv("CACHE_TTL_PRODUCT_DETAIL", "180"))
CACHE_TTL_RELATED_PRODUCTS = int(os.getenv("CACHE_TTL_RELATED_PRODUCTS", "180"))
CACHE_TTL_CATEGORIES = int(os.getenv("CACHE_TTL_CATEGORIES", "300"))
CACHE_TTL_CATALOG_FILTERS = int(os.getenv("CACHE_TTL_CATALOG_FILTERS", "300"))
CACHE_TTL_BANNERS = int(os.getenv("CACHE_TTL_BANNERS", "300"))
CACHE_TTL_BLOGS = int(os.getenv("CACHE_TTL_BLOGS", "300"))
CACHE_TTL_NOTIFICATIONS = int(os.getenv("CACHE_TTL_NOTIFICATIONS", "300"))
CACHE_TTL_STORE_SETTINGS = int(os.getenv("CACHE_TTL_STORE_SETTINGS", "300"))
CACHE_TTL_FEATURE_CONFIG = int(os.getenv("CACHE_TTL_FEATURE_CONFIG", "600"))
CACHE_TTL_SHIPPING_OPTIONS = int(os.getenv("CACHE_TTL_SHIPPING_OPTIONS", "300"))

# CORS shared pieces
CORS_ALLOW_HEADERS = list(__import__("corsheaders.defaults").defaults.default_headers) + [
    "x-store-public-id",
]
# Without this, XHR/axios cannot read Content-Disposition on cross-origin responses, so
# the dashboard cannot set a meaningful <a download> filename for blob exports.
CORS_EXPOSE_HEADERS = [
    "content-disposition",
    "x-export-filename",
]

# Middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "engine.core.middleware.internal_override_middleware.InternalOverrideMiddleware",
    "engine.core.store_api_key_auth.TenantApiKeyMiddleware",
    "engine.core.middleware.subscription_enforcement_middleware.SubscriptionEnforcementMiddleware",
    "engine.core.middleware.tenant_context_middleware.TenantContextMiddleware",
    "engine.core.middleware.request_scoped_cache_middleware.RequestScopedCacheMiddleware",
    "engine.core.rate_limit.ApiKeyRateLimitMiddleware",
]

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# tracker.js build/version id (global, deploy-scoped)
# ---------------------------------------------------------------------------
def _default_tracker_build_id() -> str:
    # Use UTC timestamp at process start; override via env in production for determinism.
    return datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")


TRACKER_BUILD_ID = (
    os.getenv("TRACKER_BUILD_ID")
    or os.getenv("GIT_SHA")
    or os.getenv("GITHUB_SHA")
    or os.getenv("CI_COMMIT_SHA")
    or os.getenv("COMMIT_SHA")
    or _default_tracker_build_id()
).strip()

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "engine.core.authentication.JWTAuthenticationAllowAPIKey",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 24,
    # Must match the number of trusted proxies that append to X-Forwarded-For before Django.
    "NUM_PROXIES": 1,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "600/min",
        "auth_token": "10/min",
        "auth_register": "10/min",
        "auth_reset": "5/min",
        "auth_otp_challenge": "12/min",
        "auth_otp_manage": "20/min",
        "direct_order": "30/hour",
        "health": "60/min",
        "heavy_search": "10/min",
        "standard_api": "600/min",
        "tracking_ingest": "300/min",
    },
}

# SECRET_KEY is environment-specific and defined in development/production modules.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=15),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "public_id",
    "USER_ID_CLAIM": "user_public_id",
}

PASSWORD_RESET_TIMEOUT = 3600

def _normalize_admin_path(value: str) -> str:
    """
    Normalize admin path into a Django URL route segment.

    - Strip leading slashes.
    - Ensure a single trailing slash.
    """
    normalized = str(value).strip()
    normalized = normalized.lstrip("/")
    # After stripping, treat empty as default.
    if not normalized:
        normalized = "admin"
    normalized = normalized.rstrip("/")
    if not normalized:
        normalized = "admin"
    return f"{normalized}/"


ADMIN_URL_PATH = _normalize_admin_path(
    os.getenv("ADMIN_PATH", os.getenv("ADMIN_URL_PATH", "admin/"))
)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TASK_IGNORE_RESULT = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200
CELERY_TASK_ANNOTATIONS = {
    "engine.core.purge_expired_trash": {
        "soft_time_limit": 480,
        "time_limit": 540,
    },
    "engine.apps.orders.export_orders_csv": {
        "soft_time_limit": 540,
        "time_limit": 600,
    },
    "engine.apps.inventory.sync_product_stock_cache_for_store": {
        "soft_time_limit": 510,
        "time_limit": 600,
    },
    "engine.apps.inventory.schedule_product_stock_cache_all_stores": {
        "soft_time_limit": 120,
        "time_limit": 150,
    },
    "engine.core.delete_r2_objects": {
        "soft_time_limit": 300,
        "time_limit": 330,
    },
    "engine.apps.tracking.coordinate_capi_flush": {
        "soft_time_limit": 30,
        "time_limit": 40,
    },
    "engine.apps.tracking.flush_store_capi": {
        "soft_time_limit": 55,
        "time_limit": 65,
    },
    "engine.apps.emails.send_email": {
        "soft_time_limit": 45,
        "time_limit": 55,
    },
    "engine.apps.emails.send_order_email": {
        "soft_time_limit": 45,
        "time_limit": 55,
    },
    "engine.apps.tracking.cleanup_old_event_logs": {
        "soft_time_limit": 120,
        "time_limit": 150,
    },
    "engine.apps.orders.cleanup_expired_order_exports": {
        "soft_time_limit": 300,
        "time_limit": 330,
    },
    "engine.apps.backup.run_base_backup": {
        "soft_time_limit": 7800,
        "time_limit": 8400,
    },
}

CELERY_TASK_ROUTES = {
    # CRITICAL queue — user is waiting
    "engine.apps.emails.send_email":            {"queue": "critical"},
    "engine.apps.emails.send_order_email":      {"queue": "critical"},
    "engine.apps.orders.export_orders_csv":     {"queue": "critical"},

    # CAPI queue — dedicated, isolated from everything
    "engine.apps.tracking.coordinate_capi_flush": {"queue": "capi"},
    "engine.apps.tracking.flush_store_capi":       {"queue": "capi"},

    # DEFAULT queue
    "engine.core.delete_r2_objects":                                    {"queue": "default"},
    "engine.apps.inventory.sync_product_stock_cache_for_store":         {"queue": "default"},
    "engine.apps.inventory.schedule_product_stock_cache_all_stores":    {"queue": "default"},
    "engine.apps.tracking.cleanup_old_event_logs":                      {"queue": "default"},

    # BACKUP queue
    "engine.apps.backup.run_base_backup": {"queue": "backup"},
}

CELERY_BEAT_SCHEDULE = {

    # CAPI flush coordinator
    "capi-flush-coordinator": {
        "task": "engine.apps.tracking.coordinate_capi_flush",
        "schedule": CAPI_FLUSH_INTERVAL_SECONDS,
        "options": {"queue": "capi"},
    },

    # Inventory sync — every hour
    "inventory-sync": {
        "task": "engine.apps.inventory.schedule_product_stock_cache_all_stores",
        "schedule": crontab(minute="0"),  # top of every hour
        "options": {"queue": "default"},
    },

    # Base backup — 6:00 AM GMT
    "base-backup": {
        "task": "engine.apps.backup.run_base_backup",
        "schedule": crontab(hour=6, minute=0),
        "options": {"queue": "backup"},
    },

    # Cleanup old event logs — 6:20 AM GMT
    "cleanup-old-event-logs": {
        "task": "engine.apps.tracking.cleanup_old_event_logs",
        "schedule": crontab(hour=6, minute=20),
        "options": {"queue": "default"},
    },

    # Purge expired trash — 6:40 AM GMT
    "purge-expired-trash": {
        "task": "engine.core.purge_expired_trash",
        "schedule": crontab(hour=6, minute=40),
        "options": {"queue": "default"},
    },

    # Cleanup order exports — 7:00 AM GMT
    "cleanup-order-exports": {
        "task": "engine.apps.orders.cleanup_expired_order_exports",
        "schedule": crontab(hour=7, minute=0),
        "options": {"queue": "default"},
    },
}

