"""Validate resolved production inputs without printing their values."""

import json
import sys
from urllib.parse import unquote, urlsplit


def validate(config, application_service="backend"):
    services = config["services"]
    database = services["db"]["environment"]
    application = services[application_service]["environment"]
    for environment, names in (
        (database, ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")),
        (application, ("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DATABASE_URL")),
    ):
        for name in names:
            if not str(environment.get(name) or "").strip():
                sys.exit(f"Missing required production input: {name}")
    url = urlsplit(application["DATABASE_URL"])
    if (
        url.scheme not in ("postgres", "postgresql")
        or not url.hostname
        or not url.username
        or not url.password
        or not url.path.strip("/")
        or url.port == 0
    ):
        sys.exit("Invalid production input: DATABASE_URL")
    if url.hostname == "db" and (
        unquote(url.username) != database["POSTGRES_USER"]
        or unquote(url.password) != database["POSTGRES_PASSWORD"]
        or unquote(url.path[1:]) != database["POSTGRES_DB"]
    ):
        sys.exit("DATABASE_URL does not match the database credentials")


if __name__ == "__main__":
    try:
        config = json.load(sys.stdin)
        validate(config, sys.argv[1] if len(sys.argv) > 1 else "backend")
    except (KeyError, TypeError, json.JSONDecodeError):
        sys.exit("Invalid production configuration; check the private input file")
    except ValueError:
        sys.exit("Invalid production input: DATABASE_URL")
    print("Production application/database inputs valid")
