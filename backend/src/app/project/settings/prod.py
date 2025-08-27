from .base import *  # noqa: F403,F401
from .base import env

DEBUG = False

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres:///app"),
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
