"""Production framework checks with the same isolated source-check inputs."""

from . import check
from .prod import *  # noqa: F403,F401

DATABASES = check.DATABASES
SECRET_KEY = check.SECRET_KEY
ALLOWED_HOSTS = check.ALLOWED_HOSTS
EMAIL_BACKEND = check.EMAIL_BACKEND
