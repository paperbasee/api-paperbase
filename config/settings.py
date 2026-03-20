"""
Minimal Django settings for local development/testing of the reusable e-commerce engine.
Not intended for production use.
"""

from datetime import timedelta
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core settings (fixed values for local/testing)
# ---------------------------------------------------------------------------

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "dev-secret-key-change-me-please-use-at-least-32-bytes-for-local",
)
DEBUG = True

# Used to make test runs deterministic (e.g. avoid DRF throttling interfering with auth tests).
TESTING = any(arg == "test" or arg.startswith("test") for arg in sys.argv)
ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Multi-tenant (Option A) platform vs tenant host routing
# ---------------------------------------------------------------------------
PLATFORM_HOSTS = [
    # Requests on these hosts are treated as "platform" (no tenant store resolved).
    # In production, set this to your dashboard/auth domain(s), e.g.:
    # "dashboard.yourplatform.com", "api.yourplatform.com"
    "localhost",
    "127.0.0.1",
]

# Root domain used when generating store subdomains (e.g. {slug}.{root})
PLATFORM_ROOT_DOMAIN = os.getenv("PLATFORM_ROOT_DOMAIN", "yourplatform.com")

# Database: simple SQLite for local development
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Applications
INSTALLED_APPS = [
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
]

# CORS: allow frontend (e.g. localhost:3000) to call API
CORS_ALLOW_ALL_ORIGINS = True  # For local dev; restrict in production
CORS_ALLOW_HEADERS = list(__import__("corsheaders.defaults").defaults.default_headers) + [
    "x-store-id",
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
    "engine.core.tenancy.TenantResolutionMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

# Password validation (kept to mirror default Django project)
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

# Static and media files (single static root is enough for local use)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Django REST Framework (simple defaults for local use)
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
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
        # Named scopes used by specific views:
        "auth_token": "10/min",        # login attempts
        "auth_register": "10/min",     # account creation
        "auth_reset": "5/min",         # password reset requests
        "direct_order": "30/hour",     # unauthenticated order placement
    },
}

# JWT configuration — runs in both dev and production.
# Requires `rest_framework_simplejwt.token_blacklist` in INSTALLED_APPS
# and `python manage.py migrate` to create the blacklist tables.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    # BLACKLIST_AFTER_ROTATION requires token_blacklist in INSTALLED_APPS + migration.
    # Steps to enable:
    #   1. Uncomment "rest_framework_simplejwt.token_blacklist" in INSTALLED_APPS above.
    #   2. Run: python manage.py migrate
    #   3. Set BLACKLIST_AFTER_ROTATION to True here.
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "SIGNING_KEY": SECRET_KEY,
}

# Password reset token expires in 1 hour (Django default is 3 days).
PASSWORD_RESET_TIMEOUT = 3600

# Basic session/CSRF configuration suitable for local development
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Admin URL path
ADMIN_URL_PATH = "admin/"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Custom user model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# Console backend for development — prints emails to stdout.
# In production, swap for SMTP or a transactional provider (SES, SendGrid, etc.)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@yourplatform.com")

# Base URL sent in password-reset / email-verification links.
# Frontend must handle /reset-password?uid=...&token=... and /verify-email?uid=...&token=...
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# ---------------------------------------------------------------------------
# Celery (async background jobs)
# ---------------------------------------------------------------------------
# For local development you can run a worker with:
#   celery -A config.celery worker -l info
#
# NOTE: This repo does not include a Celery worker container by default.
# In production, run workers separately and provide a real Redis broker.

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = TIME_ZONE

# When running tests or in local dev, it can be convenient to execute tasks
# synchronously in-process.
_always_eager_env = os.getenv("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_ALWAYS_EAGER = (
    (_always_eager_env is not None and _always_eager_env.lower() in {"1", "true", "yes"})
    or TESTING
    or DEBUG
)

