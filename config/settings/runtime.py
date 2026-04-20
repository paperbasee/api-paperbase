"""
Single environment-driven settings module (12-factor).

Use: DJANGO_SETTINGS_MODULE=config.settings.runtime

Set DEBUG=true for local development; DEBUG=false for production-like runs.
"""
from __future__ import annotations

from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403,F401


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()  # noqa: F405
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


def _storage_backend() -> str:
    raw = (os.getenv("DJANGO_STORAGE_BACKEND") or "").strip().lower()  # noqa: F405
    if raw in {"s3", "r2"}:
        return "s3"
    if raw in {"filesystem", "local", "fs"}:
        return "filesystem"
    if env_bool("USE_S3_STORAGE", False):  # noqa: F405
        return "s3"
    _r2_keys = (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_NAME",
        "R2_ENDPOINT_URL",
        "R2_PUBLIC_URL",
    )
    if all(os.getenv(k, "").strip() for k in _r2_keys):  # noqa: F405
        return "s3"
    return "filesystem"


DEBUG = env_bool("DEBUG", False)  # noqa: F405
IS_DEVELOPMENT = DEBUG

if DEBUG:
    SECRET_KEY = os.getenv(  # noqa: F405
        "SECRET_KEY",
        "dev-secret-key-change-me-please-use-at-least-32-bytes-for-local",
    )
else:
    SECRET_KEY = _require_env("SECRET_KEY")

SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY  # noqa: F405

if DEBUG:
    _allowed = env_list("ALLOWED_HOSTS")  # noqa: F405
    ALLOWED_HOSTS = _allowed if _allowed else ["localhost", "127.0.0.1", "[::1]"]
else:
    ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS must be set when DEBUG=false.")

# ---------------------------------------------------------------------------
# Database (DATABASE_URL only)
# ---------------------------------------------------------------------------

_database_url = os.getenv("DATABASE_URL", "").strip()  # noqa: F405
if not _database_url:
    if TESTING or DEBUG:  # noqa: F405
        _database_url = f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}"  # noqa: F405
    else:
        raise ImproperlyConfigured("DATABASE_URL is required when DEBUG=false.")

_default_db = dj_database_url.parse(_database_url, conn_max_age=600)
if env_bool("DATABASE_PGBOUNCER", False):  # noqa: F405
    _default_db["CONN_MAX_AGE"] = 0
    _default_db["CONN_HEALTH_CHECKS"] = True
    _default_db["ATOMIC_REQUESTS"] = False
    _db_opts = dict(_default_db.get("OPTIONS") or {})
    _db_opts["disable_server_side_cursors"] = True
    _default_db["OPTIONS"] = _db_opts
else:
    _default_db["CONN_HEALTH_CHECKS"] = True
DATABASES = {"default": _default_db}

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
# Static / media storage
# ---------------------------------------------------------------------------

_storage = _storage_backend()

if _storage == "s3":
    R2_ACCESS_KEY_ID = _require_env("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY = _require_env("R2_SECRET_ACCESS_KEY")
    R2_BUCKET_NAME = _require_env("R2_BUCKET_NAME")
    R2_ENDPOINT_URL = _require_env("R2_ENDPOINT_URL")
    R2_PUBLIC_URL = _require_env("R2_PUBLIC_URL").strip().rstrip("/")

    _r2_public = urlparse(R2_PUBLIC_URL if "://" in R2_PUBLIC_URL else f"https://{R2_PUBLIC_URL}")
    R2_CUSTOM_DOMAIN = (_r2_public.netloc or _r2_public.path).strip().strip("/")
    if not R2_CUSTOM_DOMAIN:
        raise ImproperlyConfigured("R2_PUBLIC_URL must be a valid URL or domain.")

    if "storages" not in INSTALLED_APPS:  # noqa: F405
        INSTALLED_APPS = ["storages", *INSTALLED_APPS]  # noqa: F405

    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
    AWS_S3_REGION_NAME = "auto"
    AWS_S3_CUSTOM_DOMAIN = R2_CUSTOM_DOMAIN
    AWS_QUERYSTRING_AUTH = False
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "virtual"

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "location": "media",
                "default_acl": AWS_DEFAULT_ACL,
                "querystring_auth": AWS_QUERYSTRING_AUTH,
                "file_overwrite": AWS_S3_FILE_OVERWRITE,
            },
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "location": "static",
                "default_acl": AWS_DEFAULT_ACL,
                "querystring_auth": AWS_QUERYSTRING_AUTH,
                "file_overwrite": True,
            },
        },
    }

    MEDIA_URL = f"{R2_PUBLIC_URL}/media/"
    STATIC_URL = f"{R2_PUBLIC_URL}/static/"
else:
    STATIC_URL = "/static/"
    STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"  # noqa: F405
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }
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
    STORE_API_KEY_SECRET = _require_env("STORE_API_KEY_SECRET")  # noqa: F405

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

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "null": {
                "class": "logging.NullHandler",
            },
        },
        "loggers": {
            "django": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "django.request": {"handlers": ["null"], "level": "ERROR", "propagate": False},
            "django.security": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "gunicorn.error": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "gunicorn.access": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "engine.core.tenancy": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "engine": {"handlers": ["null"], "level": "INFO", "propagate": False},
            "config": {"handlers": ["null"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["null"], "level": "WARNING"},
    }
else:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_AGE = 60 * 60 * 8
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True

if TESTING:  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
