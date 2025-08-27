# pyright: reportConstantRedefinition=false
from pathlib import Path

from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F403,F401

DEBUG = True

# Use SQLite if no DATABASE_URL was set
databases = globals().get("DATABASES")
if databases and "default" in databases:
    # already defined in base.py from env
    pass
else:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

existing_secret = globals().get("SECRET_KEY")
if existing_secret is not None:
    SECRET_KEY: str = existing_secret  # type: ignore[no-redef]
else:
    SECRET_KEY = get_random_secret_key()
