"""
Single environment-driven settings module (12-factor).

Use: DJANGO_SETTINGS_MODULE=config.settings.runtime

Set DEBUG=true for local development; DEBUG=false for production-like runs.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import dj_database_url
import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from .base import *  # noqa: F403,F401


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()  # noqa: F405
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


def _s3_custom_domain_host(raw: str) -> str:
    """Hostname for public URLs; accepts storage.example.com or https://storage.example.com/."""
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.netloc or parsed.path).strip().strip("/")


DEBUG = env_bool("DEBUG", False)  # noqa: F405
IS_DEVELOPMENT = DEBUG

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()  # noqa: F405

if DEBUG:
    SECRET_KEY = os.getenv(  # noqa: F405
        "SECRET_KEY",
        "dev-secret-key-change-me-please-use-at-least-32-bytes-for-local",
    )
else:
    SECRET_KEY = _require_env("SECRET_KEY")

SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY  # noqa: F405
STORE_API_KEY_SECRET = _require_env("STORE_API_KEY_SECRET")  # noqa: F405

if DEBUG:
    _allowed = env_list("ALLOWED_HOSTS")  # noqa: F405
    ALLOWED_HOSTS = _allowed if _allowed else ["localhost", "127.0.0.1", "[::1]"]
else:
    ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS must be set when DEBUG=false.")

# ---------------------------------------------------------------------------
# Database (DATABASE_URL only — Postgres; fail fast if unset)
# ---------------------------------------------------------------------------

try:
    DATABASE_URL = os.environ["DATABASE_URL"].strip()
except KeyError as exc:
    raise ImproperlyConfigured("DATABASE_URL environment variable is required.") from exc
if not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL must be set to a non-empty value.")

# This deployment currently points directly at Postgres (no PgBouncer in compose),
# so persistent Django DB connections reduce connect churn for API traffic.
_default_db = dj_database_url.parse(DATABASE_URL, conn_max_age=600)
_default_db["DISABLE_SERVER_SIDE_CURSORS"] = True
DATABASES = {"default": _default_db}

# DIRECT_DATABASE_URL bypasses PgBouncer for migrations and
# backup scripts. DDL transactions (ALTER TABLE, CREATE INDEX)
# are incompatible with PgBouncer transaction mode and must
# use a direct Postgres connection.
_direct_db_url = os.getenv("DIRECT_DATABASE_URL", "").strip()
if _direct_db_url:
    _direct_db = dj_database_url.parse(_direct_db_url, conn_max_age=0)
    _direct_db["DISABLE_SERVER_SIDE_CURSORS"] = False
    _direct_db.setdefault("OPTIONS", {})
    _direct_db["OPTIONS"]["connect_timeout"] = 10
    DATABASES["direct"] = _direct_db

# ---------------------------------------------------------------------------
# Redis / Channels / Cache / Celery
# ---------------------------------------------------------------------------

_redis_url = os.getenv("REDIS_URL", "").strip()  # noqa: F405

if TESTING:  # noqa: F405
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "default-tests",
        },
    }
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", ""))  # noqa: F405
    CELERY_RESULT_BACKEND = CELERY_BROKER_URL  # noqa: F405
    CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False) or True  # noqa: F405
elif _redis_url:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [_redis_url]},
        },
    }
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        },
    }
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", _redis_url)  # noqa: F405
    CELERY_RESULT_BACKEND = CELERY_BROKER_URL  # noqa: F405
    CELERY_TASK_ALWAYS_EAGER = (  # noqa: F405
        env_bool("CELERY_TASK_ALWAYS_EAGER", False) or TESTING or DEBUG  # noqa: F405
    )
elif DEBUG:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "default-local",
        },
    }
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")  # noqa: F405
    CELERY_RESULT_BACKEND = CELERY_BROKER_URL  # noqa: F405
    CELERY_TASK_ALWAYS_EAGER = (  # noqa: F405
        env_bool("CELERY_TASK_ALWAYS_EAGER", False) or DEBUG  # noqa: F405
    )
else:
    raise ImproperlyConfigured("REDIS_URL is required when DEBUG=false.")

# ---------------------------------------------------------------------------
# Static / media — S3-compatible object storage (e.g. Cloudflare R2) via django-storages + boto3
# ---------------------------------------------------------------------------

if "storages" not in INSTALLED_APPS:  # noqa: F405
    INSTALLED_APPS = [*INSTALLED_APPS, "storages"]  # noqa: F405

AWS_ACCESS_KEY_ID = _require_env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _require_env("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = _require_env("AWS_STORAGE_BUCKET_NAME")
AWS_S3_ENDPOINT_URL = _require_env("AWS_S3_ENDPOINT_URL")
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto").strip() or "auto"  # noqa: F405

_s3_public_host = _s3_custom_domain_host(_require_env("AWS_S3_CUSTOM_DOMAIN"))
if not _s3_public_host:
    raise ImproperlyConfigured(
        "AWS_S3_CUSTOM_DOMAIN must be a non-empty hostname (e.g. storage.paperbase.me) "
        "or https URL with that host."
    )
AWS_S3_CUSTOM_DOMAIN = _s3_public_host

AWS_S3_ADDRESSING_STYLE = "path"
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False
AWS_S3_FILE_OVERWRITE = False
AWS_S3_SIGNATURE_VERSION = "s3v4"

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "location": "media",
            "default_acl": None,
            "querystring_auth": False,
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_ROOT = None  # noqa: F405
MEDIA_URL = f"https://{_s3_public_host}/media/"

_mw = list(MIDDLEWARE)  # noqa: F405
_wn = "whitenoise.middleware.WhiteNoiseMiddleware"
if _wn not in _mw:
    try:
        _idx = _mw.index("django.middleware.security.SecurityMiddleware") + 1
    except ValueError:
        _idx = 0
    _mw.insert(_idx, _wn)
MIDDLEWARE = _mw

# ---------------------------------------------------------------------------
# Production-only security and secrets
# ---------------------------------------------------------------------------

CORS_ALLOW_ALL_ORIGINS = True

if not DEBUG:
    if not TENANT_API_KEY_ENFORCE:  # noqa: F405
        raise ImproperlyConfigured("TENANT_API_KEY_ENFORCE must be True when DEBUG=false.")

    CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")  # noqa: F405
    if not CSRF_TRUSTED_ORIGINS:
        raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must be set when DEBUG=false.")

    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)  # noqa: F405
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = "DENY"

    if env_bool("USE_X_FORWARDED_PROTO", False):  # noqa: F405
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_AGE = 60 * 60 * 8
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True

    _log_handlers: dict = {
        "null": {
            "class": "logging.NullHandler",
        },
    }
    _log_handler_names = ["null"]
    if SENTRY_DSN:
        _log_handlers["sentry_breadcrumb"] = {
            "class": "sentry_sdk.integrations.logging.BreadcrumbHandler",
            "level": "INFO",
        }
        _log_handlers["sentry_event"] = {
            "class": "sentry_sdk.integrations.logging.EventHandler",
            "level": "ERROR",
        }
        _log_handler_names = ["null", "sentry_breadcrumb", "sentry_event"]

    _django_level = "INFO" if SENTRY_DSN else "WARNING"
    _root_level = "INFO" if SENTRY_DSN else "WARNING"

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": _log_handlers,
        "loggers": {
            "django": {
                "handlers": list(_log_handler_names),
                "level": _django_level,
                "propagate": False,
            },
            "django.request": {
                "handlers": list(_log_handler_names),
                "level": "ERROR",
                "propagate": False,
            },
            "django.security": {
                "handlers": list(_log_handler_names),
                "level": "WARNING",
                "propagate": False,
            },
            "gunicorn.error": {
                "handlers": list(_log_handler_names),
                "level": "WARNING",
                "propagate": False,
            },
            "gunicorn.access": {
                "handlers": list(_log_handler_names),
                "level": "WARNING",
                "propagate": False,
            },
            "engine.core.tenancy": {
                "handlers": list(_log_handler_names),
                "level": "WARNING",
                "propagate": False,
            },
            "engine": {
                "handlers": list(_log_handler_names),
                "level": "INFO",
                "propagate": False,
            },
            "config": {
                "handlers": list(_log_handler_names),
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {"handlers": list(_log_handler_names), "level": _root_level},
    }
else:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_AGE = 60 * 60 * 8
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "loggers": {
            "django": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "engine": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "config": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

if SENTRY_DSN:
    _sentry_env = "development" if IS_DEVELOPMENT else "production"
    if DEBUG:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=_sentry_env,
            send_default_pii=True,
            integrations=[
                DjangoIntegration(),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
            ],
            traces_sample_rate=0.0,
        )
    else:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=_sentry_env,
            send_default_pii=True,
            integrations=[DjangoIntegration()],
            disabled_integrations=[LoggingIntegration],
            traces_sample_rate=0.0,
        )

if TESTING:  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
