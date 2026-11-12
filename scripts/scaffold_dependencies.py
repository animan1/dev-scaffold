"""Update committed package locks and declared container image pins in Docker."""

import json
import os
import re
import sys
from pathlib import Path

from scaffold_common import Rejected, immutable, read_json, require, run, write_json

ROOT = Path(__file__).resolve().parents[1]


def lock_dependencies(root, profile, execute=run):
    require(
        profile in ("react-vite", "server-rendered-django"),
        "unknown application profile",
    )
    path = root / "deploy/dependencies.json"
    data = read_json(path)
    for dependency in data["images"].values():
        source = dependency["source"]
        require(
            re.fullmatch("[a-z0-9][a-z0-9._:/-]*", source),
            "invalid declared image source",
        )
        info = json.loads(
            execute(
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                source,
                "--format",
                "{{json .Manifest}}",
                capture=True,
            )
        )
        digest = info["digest"]
        dependency["pin"] = immutable(source + "@" + digest)
    write_json(path, data)
    project = "backend"
    if profile == "server-rendered-django":
        project = "profiles/server-rendered-django"
    execute(
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--volume",
        f"{root}:/workspace",
        "--workdir",
        "/workspace",
        "--env",
        "HOME=/tmp",
        "--env",
        "UV_CACHE_DIR=/tmp/uv-cache",
        "--env",
        "UV_PROJECT_ENVIRONMENT=/tmp/lock-environment",
        "--entrypoint",
        "/usr/local/bin/uv",
        data["images"]["uv"]["pin"],
        "lock",
        "--project",
        project,
    )
    if profile == "react-vite":
        execute(
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--env",
            "CI=true",
            "--env",
            "COREPACK_HOME=/tmp/corepack",
            "--volume",
            f"{root}:/workspace",
            "--workdir",
            "/workspace/frontend",
            data["images"]["node"]["pin"],
            "sh",
            "-c",
            "corepack pnpm install --lockfile-only --ignore-scripts",
        )


def main():
    try:
        require(len(sys.argv) == 2, "invoke through make deps-lock")
        lock_dependencies(ROOT, sys.argv[1])
    except (Rejected, OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
