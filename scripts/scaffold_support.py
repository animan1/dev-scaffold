"""Small, dependency-free primitives for the public Make interface."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


class Rejected(Exception):  # noqa: N818 -- shared release contract exception name
    """An operator input or a release failed preflight."""


def require(condition, message):
    if not condition:
        raise Rejected(message)


def run(*command, cwd=None, env=None, capture=False, input=None, sensitive=()):
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        input=input,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode:
        # Never print captured stdout: Compose embeds full configuration there.
        detail = (result.stderr or "").strip()
        private = list(sensitive) + [
            value
            for key, value in (env or os.environ).items()
            if any(
                word in key
                for word in (
                    "PASSWORD",
                    "SECRET",
                    "TOKEN",
                    "CREDENTIAL",
                    "DATABASE_URL",
                )
            )
        ]
        for value in sorted((str(value) for value in private if value), key=len, reverse=True):
            detail = detail.replace(value, "[redacted]")
        raise Rejected(
            f"{command[0]} failed (exit {result.returncode})" + (": " + detail if detail else "")
        )
    return result.stdout.strip() if capture else ""


def atomic(path, content):
    path = Path(path)
    require(not path.is_symlink(), f"refusing symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def release_identity(release):
    data = {key: release[key] for key in ("revision", "profile", "platform")}
    data["images"] = release.get("image_ids", release["images"])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def compose_literals(value):
    """A resolved model must not interpolate credentials a second time."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [compose_literals(item) for item in value]
    if isinstance(value, dict):
        return {key: compose_literals(item) for key, item in value.items()}
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Rejected(f"missing or invalid JSON input: {path}") from error


def seal_release(directory):
    directory = Path(directory)
    hashes = {}
    for name in ("manifest.json", "source.tar", "images.tar"):
        with (directory / name).open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        (directory / name).chmod(0o600)
    write_json(directory / "integrity.json", hashes)


def verify_seal(directory):
    directory = Path(directory)
    hashes = read_json(directory / "integrity.json")
    require(
        set(hashes) == {"manifest.json", "source.tar", "images.tar"},
        "incomplete release integrity record",
    )
    for name, expected in hashes.items():
        with (directory / name).open("rb") as stream:
            require(
                hashlib.file_digest(stream, "sha256").hexdigest() == expected,
                "release bundle integrity mismatch",
            )


def read_env(path):
    """Read literal Compose inputs; never evaluate shell or Make expressions."""
    require(Path(path).is_file(), f"missing configuration: {path}; run make prepare-<env>")
    result = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(
            separator and re.fullmatch("[A-Z][A-Z0-9_]*", key),
            "invalid configuration key",
        )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        require("\n" not in value and "\0" not in value, "configuration must be literal")
        result[key] = value
    return result


def revision(value):
    require(
        isinstance(value, str) and re.fullmatch("[0-9a-f]{40}", value),
        "a full committed revision is required",
    )
    return value


def immutable(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"(?:[a-zA-Z0-9._:/-]+@)?sha256:[0-9a-f]{64}", value),
        "mutable or invalid image identity",
    )
    return value


def validate_release(data, selected=None, platform=None):
    require(isinstance(data, dict), "release manifest must be an object")
    require(data.get("schema") == 1, "unsupported release manifest")
    revision(data.get("revision"))
    require(
        data.get("profile") in ("react-vite", "server-rendered-django"),
        "unknown release profile",
    )
    require(not selected or data["profile"] == selected, "release profile mismatch")
    require(
        re.fullmatch(r"linux/(amd64|arm64)", data.get("platform", "")),
        "unsupported release architecture",
    )
    require(not platform or data["platform"] == platform, "release architecture mismatch")
    images = data.get("images", {})
    require(isinstance(images, dict), "invalid runtime image inventory")
    require(
        {"backend", "web", "db", "backup"} <= images.keys(),
        "release runtime image inventory is incomplete",
    )
    for value in images.values():
        immutable(value)
    if "image_ids" in data:
        require(
            isinstance(data["image_ids"], dict) and data["image_ids"].keys() == images.keys(),
            "incomplete published image identity mapping",
        )
        for value in data["image_ids"].values():
            require(
                re.fullmatch("sha256:[0-9a-f]{64}", value),
                "invalid published content identity",
            )
    require(
        data.get("backup_profile") in ("none", "immutable-backup"),
        "unknown backup profile",
    )
    return data


@contextlib.contextmanager
def locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "operator.lock").open("a") as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Rejected("another operation owns this environment") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def target(goal):
    plain = {
        "help",
        "profiles",
        "setup",
        "hooks",
        "build",
        "publish",
        "download",
        "download-latest",
        "check",
        "format-check",
        "lint",
        "typecheck",
        "migrations-check",
        "framework-check",
        "test",
        "image-check",
        "deps-lock",
        "migrations",
        "format",
        "clean",
    }
    if goal in plain:
        return goal, None, None
    for operation in (
        "integration-test",
        "monitor-check",
        "backup-test",
        "prepare",
        "restart",
        "rollback",
        "snapshots",
        "restore",
        "migrate",
        "superuser",
        "console",
        "history",
        "config",
        "verify",
        "backup",
        "shell",
        "logs",
        "down",
        "up",
        "ps",
        "rm",
        "ci",
    ):
        for environment in ("dev", "prod", "ephemeral"):
            prefix = operation + "-" + environment
            if goal == prefix:
                require(
                    operation != "ci" or environment == "ephemeral",
                    "CI uses ephemeral only",
                )
                return operation, environment, None
            if goal.startswith(prefix + "-"):
                service = goal[len(prefix) + 1 :]
                require(
                    operation in ("up", "down", "restart", "ps", "logs", "shell"),
                    "this command has no service selector",
                )
                require(
                    re.fullmatch("[a-z][a-z0-9_-]*", service),
                    "invalid service selector",
                )
                return operation, environment, service
    raise Rejected(f"unknown command: {goal}; run make help")
