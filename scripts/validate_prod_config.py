"""Validate resolved production inputs without printing their values."""

import getpass
import json
import os
import re
import stat
import subprocess
import sys
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ConfigurationError(Exception):
    """Invalid preparation input with a message safe to display."""


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
            raise ConfigurationError(
                "Host-ingress origin requires loopback TCP port bindings"
            )
        hosts = [
            host.strip().lower()
            for host in application["DJANGO_ALLOWED_HOSTS"].split(",")
        ]
        if not all(valid_host(host) for host in hosts):
            raise ConfigurationError("Invalid DJANGO_ALLOWED_HOSTS for host ingress")
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
                raise ConfigurationError("Invalid HOST_INGRESS_HOST")
            if not any(
                pattern == "*"
                or domain == pattern
                or (
                    pattern.startswith(".")
                    and (domain == pattern[1:] or domain.endswith(pattern))
                )
                for pattern in hosts
            ):
                raise ConfigurationError(
                    "HOST_INGRESS_HOST is not accepted by DJANGO_ALLOWED_HOSTS"
                )
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
                raise ConfigurationError(
                    "DJANGO_CSRF_TRUSTED_ORIGINS requires HTTPS origins without paths or credentials"
                )
    except (KeyError, TypeError, ValueError):
        raise ConfigurationError("Invalid host-ingress configuration") from None


def prompt_backup_input(name):
    path = os.environ.get("PROD_ENV_FILE")
    if not path or os.environ.get("PREPARE_INTERACTIVE") != "1":
        raise ConfigurationError(
            f"Missing required backup input: {name}; supply it for non-interactive use"
        )
    try:
        value = getpass.getpass(f"{name}: ")
    except (EOFError, OSError, KeyboardInterrupt):
        raise ConfigurationError(f"Unable to read {name} from the terminal") from None
    if not value.strip():
        raise ConfigurationError(f"{name} cannot be empty")
    # Compose double-quoted dotenv values support JSON escapes and $$ for a
    # literal dollar. Keep existing configuration bytes and credentials intact.
    encoded = json.dumps(value.replace("$", "$$"), ensure_ascii=False)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "a", encoding="utf-8") as private:
            private.write(f"\n{name}={encoded}\n")
    except OSError:
        raise ConfigurationError(
            f"Unable to save {name} in the private configuration"
        ) from None


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
                raise ConfigurationError(f"Missing required production input: {name}")
    url = urlsplit(application["DATABASE_URL"])
    if (
        url.scheme not in ("postgres", "postgresql")
        or not url.hostname
        or not url.username
        or not url.password
        or not url.path.strip("/")
        or url.port == 0
    ):
        raise ConfigurationError("Invalid production input: DATABASE_URL")
    if url.hostname == "db" and (
        unquote(url.username) != database["POSTGRES_USER"]
        or unquote(url.password) != database["POSTGRES_PASSWORD"]
        or unquote(url.path[1:]) != database["POSTGRES_DB"]
    ):
        raise ConfigurationError("DATABASE_URL does not match the database credentials")
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
                if source in ("BACKUP_PASSWORD", "BACKUP_REPOSITORY"):
                    prompt_backup_input(source)
                else:
                    raise ConfigurationError(f"Missing required backup input: {source}")


def prepare_backup_paths(config):
    services = config["services"]
    if "backup" not in services:
        return
    try:
        uid, gid = map(int, services["backup"]["user"].split(":"))
        if uid <= 0 or gid < 0:
            raise ConfigurationError("Backup paths require a dedicated service UID:GID")
        changes = []
        targets = ("/backups", "/config/rclone")
        owner = ["-o", str(uid), "-g", str(gid)]
        for mount in services["backup"].get("volumes", []):
            if mount.get("type") != "bind" or mount["target"] not in targets:
                continue
            path = Path(mount["source"])
            if path.is_symlink() or Path.cwd().is_relative_to(path):
                raise ConfigurationError("Backup preparation cannot own this path")
            shared = False
            writable = False
            for name, service in services.items():
                if name in ("backup", "backup-init"):
                    continue
                for other in service.get("volumes", []):
                    if other.get("type") != "bind" or Path(other["source"]) != path:
                        continue
                    other_uid, other_gid = map(int, service["user"].split(":"))
                    groups = {other_gid, *map(int, service.get("group_add", []))}
                    if other_uid <= 0 or gid not in groups:
                        raise ConfigurationError(
                            "Shared backup paths require a common service group"
                        )
                    shared = True
                    writable |= not other.get("read_only", False)
            mode = (0o2770 if writable else 0o2750) if shared else 0o700
            existing = path.stat() if path.exists() else None
            if existing:
                if not stat.S_ISDIR(existing.st_mode):
                    raise ConfigurationError("Backup preparation requires directories")
                if (
                    mount["target"] == "/backups"
                    and (existing.st_uid, existing.st_gid) != (uid, gid)
                    and any(path.iterdir())
                ):
                    raise ConfigurationError(
                        "Nonempty backup paths need an explicit ownership migration"
                    )
                actual = (
                    existing.st_uid,
                    existing.st_gid,
                    stat.S_IMODE(existing.st_mode),
                )
                if actual == (uid, gid, mode):
                    continue
            if mount["target"] == "/config/rclone" and os.access(path, os.X_OK):
                configured = (
                    services["backup"]
                    .get("environment", {})
                    .get("RCLONE_CONFIG", "/config/rclone/rclone.conf")
                )
                try:
                    relative = Path(configured).relative_to(mount["target"])
                except ValueError:
                    relative = None
                if relative is not None:
                    if ".." in relative.parts:
                        raise ConfigurationError(
                            "RCLONE_CONFIG escapes its host directory"
                        )
                    credential = path / relative
                    if credential.is_symlink():
                        raise ConfigurationError("RCLONE_CONFIG cannot be a symlink")
                    if credential.exists():
                        if not credential.is_file():
                            raise ConfigurationError("RCLONE_CONFIG must be a file")
                        file_mode = (0o660 if writable else 0o640) if shared else 0o600
                        changes.extend(
                            [
                                ["chown", f"{uid}:{gid}", str(credential)],
                                ["chmod", f"{file_mode:o}", str(credential)],
                            ]
                        )
            changes.append(["install", "-d", *owner, "-m", f"{mode:o}", str(path)])
        for command in changes:
            if os.geteuid() != 0:
                sudo = (
                    ["sudo", "-p", "Directory ownership requires sudo: "]
                    if os.environ.get("PREPARE_INTERACTIVE") == "1"
                    else ["sudo", "-n"]
                )
                command = [*sudo, "--", *command]
            subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    except (KeyError, TypeError, ValueError):
        raise ConfigurationError(
            "Backup path consumers must declare numeric UID:GID ownership"
        ) from None
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt):
        raise ConfigurationError(
            "Backup directory preparation requires host ownership privileges"
        ) from None


if __name__ == "__main__":
    try:
        config = json.load(sys.stdin)
        validate(config, sys.argv[1] if len(sys.argv) > 1 else "backend")
        prepare_backup_paths(config)
    except ConfigurationError as error:
        sys.exit(str(error))
    except (KeyError, TypeError, json.JSONDecodeError):
        sys.exit("Invalid production configuration; check the private input file")
    except ValueError:
        sys.exit("Invalid production input: DATABASE_URL")
    print("Production application/database inputs valid")
