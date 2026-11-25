"""Isolated source checks, independent of private development configuration."""

from .dev import *  # noqa: F403,F401

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SECRET_KEY = "source-check-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ALLOWED_HOSTS = ["localhost"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
