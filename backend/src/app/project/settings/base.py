from __future__ import annotations

from pathlib import Path

import django_stubs_ext
import environ
from django.core.exceptions import ImproperlyConfigured

django_stubs_ext.monkeypatch()

# /backend/src/app/project/settings/base.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # /backend/src/app

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)

# Load env from backend/.env if present
environ.Env.read_env(str(BASE_DIR.parent.parent / ".env"))

try:
    SECRET_KEY = env("DJANGO_SECRET_KEY")
except ImproperlyConfigured:
    pass
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])
DEPLOYMENT_ENVIRONMENT = env("DEPLOYMENT_ENVIRONMENT", default="development")
RELEASE_REVISION = env("RELEASE_REVISION", default="unknown")
RELEASE_DEPLOYED_AT = env("RELEASE_DEPLOYED_AT", default="")
MONITOR_SITE_NAME = env("MONITOR_SITE_NAME", default="Application")
MONITOR_NOTIFICATION_BACKEND = env("MONITOR_NOTIFICATION_BACKEND", default="email")
SITE_ADMIN_EMAIL = env("SITE_ADMIN_EMAIL", default="")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="app@localhost")
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="")
EMAIL_PORT = env.int("DJANGO_EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("DJANGO_EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env.int("DJANGO_EMAIL_TIMEOUT", default=10)
BACKUP_STATUS_DIR = env("BACKUP_STATUS_DIR", default="")
BACKUP_MAX_AGE_SECONDS = env.int("BACKUP_MAX_AGE_SECONDS", default=172_800)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app.monitoring.apps.MonitoringConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "app.project.urls"
WSGI_APPLICATION = "app.project.wsgi.application"
ASGI_APPLICATION = "app.project.asgi.application"

# Templates (required for admin)
TEMPLATES: list[dict[str, object]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(BASE_DIR / "templates")],  # optional project-level templates directory
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "unique-snowflake",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = str(BASE_DIR / "static")
STATICFILES_DIRS: list[str] = [str(BASE_DIR / "project" / "static")]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
