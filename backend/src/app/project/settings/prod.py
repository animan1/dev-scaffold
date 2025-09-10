from __future__ import annotations

from .base import *  # noqa: F403,F401
from .base import env

# --- Core toggles ---
DEBUG = False

# SECRET_KEY should already be loaded in base.py from env (no fallback here)

# Allowed hosts & CSRF
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])  # type: ignore[no-redef]
CSRF_TRUSTED_ORIGINS: list[str] = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=[f"https://{h}" for h in ALLOWED_HOSTS if not h.startswith("http")],
)

# Database (require Postgres in prod)
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres:///app"),
}

# --- Security / proxy ---
# We terminate TLS at nginx; tell Django the original scheme
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# HSTS: set to 31536000 and enable the flags once real HTTPS is in place
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)

# --- Static files ---
# nginx will serve collected static from this directory (ensure collectstatic in build/deploy)
# STATIC_URL should be defined in base.py
# Example default here if not already set in base:
# from pathlib import Path
# STATIC_ROOT = Path(BASE_DIR) / "staticfiles"

# --- Logging ---
# Ship useful logs to stdout/stderr (picked up by Docker)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO"},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "gunicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
