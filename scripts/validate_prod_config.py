"""Validate resolved production inputs without printing their values."""

import getpass
import glob
import grp
import json
import os
import pwd
import re
import shlex
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


def validate_backup_path(value):
    if not Path(value).is_absolute() or "\n" in value or "\r" in value:
        raise ConfigurationError("BACKUP_LOCAL_PATH must be an absolute host directory")


def run_host_command(command, privileged=False):
    if privileged and os.geteuid() != 0:
        sudo = (
            ["sudo"] if os.environ.get("PREPARE_INTERACTIVE") == "1" else ["sudo", "-n"]
        )
        command = [*sudo, "--", *command]
    print("+ " + shlex.join(command), file=sys.stderr, flush=True)
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)


def prepare_backup_group(values):
    project = values.get("COMPOSE_PROJECT_NAME") or Path.cwd().name
    name = values.get("BACKUP_GROUP") or f"{project}-backup"
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name):
        raise ConfigurationError(
            "BACKUP_GROUP must be a valid host group name (up to 32 characters)"
        )
    try:
        group = grp.getgrnam(name)
    except KeyError:
        command = (
            ["dseditgroup", "-o", "create", name]
            if sys.platform == "darwin"
            else ["groupadd", name]
        )
        run_host_command(command, privileged=True)
        group = grp.getgrnam(name)
    if os.geteuid() != 0:
        operator = pwd.getpwuid(os.getuid())
        if (
            operator.pw_gid != group.gr_gid
            and operator.pw_name not in group.gr_mem
            and group.gr_gid not in os.getgroups()
        ):
            command = (
                [
                    "dseditgroup",
                    "-o",
                    "edit",
                    "-a",
                    operator.pw_name,
                    "-t",
                    "user",
                    name,
                ]
                if sys.platform == "darwin"
                else ["usermod", "-a", "-G", name, operator.pw_name]
            )
            run_host_command(command, privileged=True)
        if group.gr_gid not in {os.getgid(), *os.getgroups()}:
            print(
                f"Open a new login session to activate membership in {name}.",
                file=sys.stderr,
            )
    gid = str(group.gr_gid)
    if os.environ.get("BACKUP_RUNTIME_GID", gid) != gid:
        raise ConfigurationError(
            "BACKUP_RUNTIME_GID must match the selected BACKUP_GROUP"
        )
    if values.get("BACKUP_GROUP") != name:
        save_backup_input("BACKUP_GROUP", name)
    if values.get("BACKUP_RUNTIME_GID") != gid:
        save_backup_input("BACKUP_RUNTIME_GID", gid)


def read_backup_path(name, default=None):
    try:
        import readline
    except ImportError:
        print(
            "Directory completion unavailable in this Python; enter the full path.",
            file=sys.stderr,
        )
    else:

        def complete(text, state):
            matches = [
                path + os.sep
                for path in sorted(
                    glob.glob(glob.escape(os.path.expanduser(text)) + "*")
                )
                if os.path.isdir(path)
            ]
            return matches[state] if state < len(matches) else None

        readline.set_completer_delims("\t\n")
        readline.set_completer(complete)
        readline.parse_and_bind(
            "bind ^I rl_complete" if "libedit" in readline.__doc__ else "tab: complete"
        )
    prompt = f"{name} [{default}]: " if default else f"{name}: "
    return os.path.expanduser(input(prompt) or default or "")


def prompt_backup_input(name, secret=True, default=None):
    path = os.environ.get("PROD_ENV_FILE")
    if not path or os.environ.get("PREPARE_INTERACTIVE") != "1":
        raise ConfigurationError(
            f"Missing required backup input: {name}; supply it for non-interactive use"
        )
    try:
        if secret:
            value = getpass.getpass(f"{name}: ")
        elif name == "BACKUP_LOCAL_PATH":
            value = read_backup_path(name, default)
        else:
            value = input(f"{name} [{default}]: ") or default or ""
    except (EOFError, OSError, KeyboardInterrupt):
        raise ConfigurationError(f"Unable to read {name} from the terminal") from None
    if not value.strip():
        raise ConfigurationError(f"{name} cannot be empty")
    if name == "BACKUP_LOCAL_PATH":
        validate_backup_path(value)
    save_backup_input(name, value)
    return value


def save_backup_input(name, value):
    path = os.environ["PROD_ENV_FILE"]
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


def prepare_backup_path():
    # Use Compose's dotenv parsing and precedence without loading services or
    # requiring a path before the operator has had an opportunity to supply it.
    try:
        resolved = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.environ["PROD_ENV_FILE"],
                "-f",
                "-",
                "config",
                "--environment",
            ],
            input="services: {}\n",
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise ConfigurationError("Unable to read backup storage inputs") from None
    values = dict(
        line.split("=", 1) for line in resolved.stdout.splitlines() if "=" in line
    )
    value = values.get("BACKUP_LOCAL_PATH", "")
    if value.strip():
        validate_backup_path(value)
    else:
        default = Path.home() / ".local/share" / Path.cwd().name / "backups"
        value = prompt_backup_input(
            "BACKUP_LOCAL_PATH", secret=False, default=str(default)
        )
    prepare_backup_group(values)


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
        for mount in services["backup"].get("volumes", []):
            if mount.get("type") != "bind" or mount["target"] != "/backups":
                continue
            path = Path(mount["source"].replace("$$", "$"))
            if path.is_symlink() or Path.cwd().is_relative_to(path):
                raise ConfigurationError("Backup preparation cannot own this path")
            for name, service in services.items():
                if name in ("backup", "backup-init"):
                    continue
                for other in service.get("volumes", []):
                    if (
                        other.get("type") != "bind"
                        or Path(other["source"].replace("$$", "$")) != path
                    ):
                        continue
                    other_uid, other_gid = map(int, service["user"].split(":"))
                    groups = {other_gid, *map(int, service.get("group_add", []))}
                    if other_uid <= 0 or gid not in groups:
                        raise ConfigurationError(
                            "Shared backup paths require a common service group"
                        )
            # The operator and fixed-UID worker always share host storage.
            mode = 0o2770
            existing = path.stat() if path.exists() else None
            if existing:
                if not stat.S_ISDIR(existing.st_mode):
                    raise ConfigurationError("Backup preparation requires directories")
                if existing.st_gid != gid and any(path.iterdir()):
                    raise ConfigurationError(
                        "Nonempty backup paths need an explicit ownership migration"
                    )
                actual = (
                    existing.st_uid,
                    existing.st_gid,
                    stat.S_IMODE(existing.st_mode),
                )
                if actual == (existing.st_uid, gid, mode):
                    continue
            owner = ["-g", str(gid)]
            privileged = (
                existing is not None and existing.st_uid != os.geteuid()
            ) or gid not in {os.getgid(), *os.getgroups()}
            if privileged:
                owner += ["-o", str(existing.st_uid if existing else os.getuid())]
            changes.append(["install", "-d", *owner, "-m", f"{mode:o}", str(path)])
        for command in changes:
            run_host_command(command, privileged="-o" in command)
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
        if sys.argv[1:] == ["--backup-path"]:
            prepare_backup_path()
            sys.exit(0)
        config = json.load(sys.stdin)
        validate(config, sys.argv[1] if len(sys.argv) > 1 else "backend")
        prepare_backup_paths(config)
    except ConfigurationError as error:
        sys.exit(str(error))
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt):
        sys.exit("Host backup group preparation requires administrator privileges")
    except (KeyError, TypeError, json.JSONDecodeError):
        sys.exit("Invalid production configuration; check the private input file")
    except ValueError:
        sys.exit("Invalid production input: DATABASE_URL")
    print("Production application/database inputs valid")
