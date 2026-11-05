"""Validate resolved production inputs without printing their values."""

import json
import os
import re
import sys
from ipaddress import IPv6Address, ip_address
from urllib.parse import unquote, urlsplit


def valid_host(host):
    if host == "*":
        return True
    if host.startswith("[") and host.endswith("]"):
        IPv6Address(host[1:-1])
        return True
    host = host.removeprefix(".")
    return len(host) <= 253 and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.I)
        for label in host.split(".")
    )


def validate_ingress(config, application):
    try:
        services = config["services"]
        origin = services.get("nginx", services.get("web", {}))
        ports = origin.get("ports", [])
        if not ports or any(
            not ip_address(port.get("host_ip", "0.0.0.0")).is_loopback
            or port.get("protocol", "tcp") != "tcp"
            or not 1 <= int(port.get("published", 0)) <= 65535
            for port in ports
        ):
            sys.exit("Host-ingress origin requires loopback TCP port bindings")
        hosts = [
            host.strip().lower()
            for host in application["DJANGO_ALLOWED_HOSTS"].split(",")
        ]
        if not all(valid_host(host) for host in hosts):
            sys.exit("Invalid DJANGO_ALLOWED_HOSTS for host ingress")
        public = os.environ.get("HOST_INGRESS_HOST", "")
        if public:
            address = urlsplit("//" + public)
            domain = address.hostname or ""
            domain = f"[{domain}]" if ":" in domain else domain.rstrip(".")
            if (
                not valid_host(domain)
                or domain.startswith(("*", "."))
                or address.username is not None
                or address.password is not None
                or address.path
                or address.query
                or address.fragment
                or address.port is not None
            ):
                sys.exit("Invalid HOST_INGRESS_HOST")
            if not any(
                pattern == "*"
                or domain == pattern
                or (
                    pattern.startswith(".")
                    and (domain == pattern[1:] or domain.endswith(pattern))
                )
                for pattern in hosts
            ):
                sys.exit("HOST_INGRESS_HOST is not accepted by DJANGO_ALLOWED_HOSTS")
        for value in application.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(","):
            if not value.strip():
                continue
            origin = urlsplit(value.strip())
            host = (origin.hostname or "").removeprefix("*.")
            host = f"[{host}]" if ":" in host else host
            if (
                origin.scheme != "https"
                or not valid_host(host)
                or origin.username is not None
                or origin.password is not None
                or origin.path
                or origin.query
                or origin.fragment
                or origin.port == 0
            ):
                sys.exit(
                    "DJANGO_CSRF_TRUSTED_ORIGINS requires HTTPS origins without paths or credentials"
                )
    except (KeyError, TypeError, ValueError):
        sys.exit("Invalid host-ingress configuration")


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
    if os.environ.get("HOST_INGRESS") == "1":
        validate_ingress(config, application)
    if "backup" in services:
        backup = services["backup"]["environment"]
        for name in (
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_PASSWORD",
            "RESTIC_REPOSITORY",
            "RESTIC_PASSWORD",
        ):
            if not str(backup.get(name) or "").strip():
                source = {
                    "RESTIC_REPOSITORY": "BACKUP_REPOSITORY",
                    "RESTIC_PASSWORD": "BACKUP_PASSWORD",
                }.get(name, name)
                sys.exit(f"Missing required backup input: {source}")


if __name__ == "__main__":
    try:
        config = json.load(sys.stdin)
        validate(config, sys.argv[1] if len(sys.argv) > 1 else "backend")
    except (KeyError, TypeError, json.JSONDecodeError):
        sys.exit("Invalid production configuration; check the private input file")
    except ValueError:
        sys.exit("Invalid production input: DATABASE_URL")
    print("Production application/database inputs valid")
