from django.core.exceptions import ImproperlyConfigured
from urllib.parse import urlparse
import dj_database_url

from .base import *  # noqa: F403,F401


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()  # noqa: F405
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


DEBUG = env_bool("DEBUG", False)  # noqa: F405
if DEBUG:
    raise ImproperlyConfigured("DEBUG must be False in production.")

SECRET_KEY = _require_env("SECRET_KEY")
SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY  # noqa: F405
STORE_API_KEY_SECRET = _require_env("STORE_API_KEY_SECRET")  # noqa: F405

if not TENANT_API_KEY_ENFORCE:  # noqa: F405
    raise ImproperlyConfigured("TENANT_API_KEY_ENFORCE must be True in production.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

# ---------------------------------------------------------------------------
# Security hardening (production-only)
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")  # noqa: F405
if not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must be set in production.")

# Cloudflare R2 storage (S3-compatible) for production media/static assets.
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

# Database
# Direct Postgres: conn_max_age=600 reuses connections within workers.
# PgBouncer (transaction pooling): set DATABASE_PGBOUNCER=1 so Django releases
# the server connection after each request (CONN_MAX_AGE=0) and disables
# server-side cursors, which are unsafe across pooled transactions.
_default_db = dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)  # noqa: F405
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

_redis_url = _require_env("REDIS_URL")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [_redis_url]},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _redis_url,
    },
}

CELERY_BROKER_URL = _redis_url
CELERY_RESULT_BACKEND = CELERY_BROKER_URL  # noqa: F405
CELERY_TASK_ALWAYS_EAGER = False

# CORS: allow any browser Origin so store-owner frontends (localhost, Vercel, custom
# domains) work without maintaining a per-tenant allowlist. Security remains JWT +
# publishable API key + tenant middleware; do not use Origin for authorization.
# Do not set CORS_ALLOW_CREDENTIALS (keep default False); wildcard ACAO is incompatible
# with credentialed cross-origin requests in browsers.
CORS_ALLOW_ALL_ORIGINS = True

# If running behind a reverse proxy/ingress that terminates TLS, let Django trust
# the forwarded scheme header (only when explicitly enabled).
if env_bool("USE_X_FORWARDED_PROTO", False):  # noqa: F405
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# ---------------------------------------------------------------------------
# Logging: no stdout/stderr (operational signals use DB e.g. StoreEventLog).
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "loggers": {
        # Framework and server noise: warnings+ only.
        "django": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["null"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        # Gunicorn (starts before Django; only effective if using gunicorn.conf.py / matching flags).
        "gunicorn.error": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "gunicorn.access": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        # Per-request store resolution is DEBUG-only (see engine.core.tenancy.log_store_resolution).
        "engine.core.tenancy": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        # Project code: info by default.
        "engine": {"handlers": ["null"], "level": "INFO", "propagate": False},
        "config": {"handlers": ["null"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["null"], "level": "WARNING"},
}

