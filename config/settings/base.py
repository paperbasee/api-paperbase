from datetime import timedelta
from pathlib import Path
import os
import sys

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


# Used to make test runs deterministic (e.g. avoid DRF throttling interfering with auth tests).
TESTING = any(arg == "test" or arg.startswith("test") for arg in sys.argv)

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
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "engine.core",
    "engine.apps.stores",
    "engine.apps.billing",
    "engine.apps.products",
    "engine.apps.orders",
    "engine.apps.cart",
    "engine.apps.wishlist",
    "engine.apps.notifications",
    "engine.apps.support",
    "engine.apps.accounts",
    "engine.apps.customers",
    "engine.apps.inventory",
    "engine.apps.shipping",
    "engine.apps.coupons",
    "engine.apps.reviews",
    "engine.apps.banners",
    "engine.apps.analytics",
    "engine.apps.couriers",
    "engine.apps.marketing_integrations",
    "engine.apps.emails",
]

ASGI_APPLICATION = "config.asgi.application"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Tenant API auth
TENANT_API_PREFIX = "/api/v1/"
TENANT_API_KEY_ENFORCE = env_bool("TENANT_API_KEY_ENFORCE", not TESTING)
STORE_API_KEY_SECRET = os.getenv("STORE_API_KEY_SECRET", "").strip()
STORE_API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS = int(
    os.getenv("STORE_API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS", "60")
)

# ---------------------------------------------------------------------------
# Data cache TTLs (seconds) — used by engine.core.cache_service
# ---------------------------------------------------------------------------
CACHE_TTL_PRODUCT_LIST = int(os.getenv("CACHE_TTL_PRODUCT_LIST", "120"))
CACHE_TTL_PRODUCT_DETAIL = int(os.getenv("CACHE_TTL_PRODUCT_DETAIL", "180"))
CACHE_TTL_RELATED_PRODUCTS = int(os.getenv("CACHE_TTL_RELATED_PRODUCTS", "180"))
CACHE_TTL_CATEGORIES = int(os.getenv("CACHE_TTL_CATEGORIES", "300"))
CACHE_TTL_BANNERS = int(os.getenv("CACHE_TTL_BANNERS", "300"))
CACHE_TTL_NOTIFICATIONS = int(os.getenv("CACHE_TTL_NOTIFICATIONS", "300"))
CACHE_TTL_STORE_SETTINGS = int(os.getenv("CACHE_TTL_STORE_SETTINGS", "300"))
CACHE_TTL_FEATURE_CONFIG = int(os.getenv("CACHE_TTL_FEATURE_CONFIG", "600"))
CACHE_TTL_REVIEWS = int(os.getenv("CACHE_TTL_REVIEWS", "120"))
CACHE_TTL_REVIEW_SUMMARY = int(os.getenv("CACHE_TTL_REVIEW_SUMMARY", "120"))
CACHE_TTL_SHIPPING_OPTIONS = int(os.getenv("CACHE_TTL_SHIPPING_OPTIONS", "300"))

# CORS shared pieces
CORS_ALLOW_HEADERS = list(__import__("corsheaders.defaults").defaults.default_headers) + [
    "x-store-public-id",
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
    "engine.core.store_api_key_auth.TenantApiKeyMiddleware",
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
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

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
    },
}

# SECRET_KEY is environment-specific and defined in development/production modules.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
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
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@akkho.com")
SUPPORT_FROM_EMAIL = os.getenv("SUPPORT_FROM_EMAIL", "support@akkho.com")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

