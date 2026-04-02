from .base import *  # noqa: F403,F401

# Core development profile
SECRET_KEY = os.getenv(  # noqa: F405
    "SECRET_KEY",
    "dev-secret-key-change-me-please-use-at-least-32-bytes-for-local",
)
DEBUG = True
ALLOWED_HOSTS = ["*"]
IS_DEVELOPMENT = True

SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY  # noqa: F405

# Local-friendly database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Channels: always in-memory in development (no external dependency).
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

# Cache: local by default, Redis optional.
_tenant_cache_redis_url = os.getenv("REDIS_URL", "").strip()  # noqa: F405
if TESTING:  # noqa: F405
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "default-tests",
        },
    }
else:
    if _tenant_cache_redis_url:
        CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": _tenant_cache_redis_url,
            },
        }
    else:
        CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "default-local",
            },
        }

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", ""))  # noqa: F405
CELERY_RESULT_BACKEND = CELERY_BROKER_URL  # noqa: F405
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False) or TESTING or DEBUG  # noqa: F405

CORS_ALLOW_ALL_ORIGINS = True

CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Tests: never call Resend; use in-memory outbox via Django mail.
if TESTING:  # noqa: F405
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
