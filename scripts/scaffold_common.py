"""Shared standard-library execution and file primitives for Make commands."""

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


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Rejected(f"missing or invalid JSON input: {path}") from error


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
