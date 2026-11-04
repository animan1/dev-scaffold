"""Docker tooling and source checks; these commands never start a deployment."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from profile_metadata import metadata
from scaffold_common import (
    Rejected,
    atomic,
    immutable,
    read_json,
    require,
    revision,
    run,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "scaffold_source.py",
    "scaffold_common.py",
    "scaffold_dependencies.py",
    "profile_metadata.py",
    "changed_coverage.py",
    "scaffold.py",
    "scaffold_support.py",
)
GATES = (
    "format-check",
    "lint",
    "typecheck",
    "migrations-check",
    "framework-check",
    "test",
    "migrations",
    "format",
)

GENERATED_OWNERSHIP = """
import os
import sys
from pathlib import Path
uid, gid = map(int, sys.argv[1:])
for name in ('backend/.coverage', 'backend/coverage.xml', 'backend/.pytest_cache',
             'backend/.mypy_cache', 'backend/.ruff_cache', 'frontend/coverage'):
    path = Path('/workspace') / name
    if path.is_symlink() or not path.exists():
        continue
    paths = [path]
    if path.is_dir():
        paths.extend(Path(base) / child
                     for base, dirs, files in os.walk(path, followlinks=False)
                     for child in dirs + files)
    for item in paths:
        info = item.lstat()
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.chown(item, uid, gid, follow_symlinks=False)
"""


class SourceOperator:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.local = self.root / ".local"
        self.profile = os.environ.get("SCAFFOLD_PROFILE", "react-vite")
        self.backup_profile = os.environ.get("SCAFFOLD_BACKUP_PROFILE", "none")
        self.profiles = metadata(
            self.profile,
            self.backup_profile,
            os.environ.get("CI_PROFILES", "react-vite server-rendered-django").split(),
            os.environ.get("CI_BACKUP_PROFILES", "immutable-backup").split(),
        )
        self.project = os.environ.get("PROJECT_NAME", self.root.name)
        require(re.fullmatch("[a-z0-9][a-z0-9_-]*", self.project), "invalid project name")
        self.app = "backend" if self.profile == "react-vite" else "app"
        self.web = "nginx" if self.profile == "react-vite" else "web"
        self.tool_image = f"local/{self.project}-tooling:{self.profile}"
        self.node_image = f"local/{self.project}-tooling:frontend"
        self.pyproject = (
            "backend/pyproject.toml"
            if self.profile == "react-vite"
            else "profiles/server-rendered-django/pyproject.toml"
        )
        self.prefix = os.environ.get("RELEASE_IMAGE_PREFIX") or f"local/{self.project}"
        require(
            re.fullmatch("[a-z0-9][a-z0-9._:/-]*", self.prefix),
            "invalid release image prefix",
        )
        self._platform = None
        self._store_checked = False

    def git(self, *arguments):
        return self.execute("git", *arguments, cwd=self.root, capture=True)

    def head(self):
        return revision(self.git("rev-parse", "HEAD"))

    def clean_source(self):
        require(
            not self.git("status", "--porcelain"),
            "commit all source changes before building a release",
        )
        return self.head()

    def architecture(self):
        self.require_image_store()
        if self._platform:
            return self._platform
        value = os.environ.get("RELEASE_PLATFORM")
        if not value:
            value = self.native_architecture()
        require(
            value in ("linux/amd64", "linux/arm64"),
            "unsupported destination architecture",
        )
        self._platform = value
        return value

    def native_architecture(self):
        return self.execute(
            "docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}", capture=True
        )

    def require_image_store(self):
        if not self._store_checked:
            status = (
                json.loads(
                    self.execute(
                        "docker", "info", "--format", "{{json .DriverStatus}}", capture=True
                    )
                )
                or []
            )
            require(
                ["driver-type", "io.containerd.snapshotter.v1"] in status,
                "Docker's containerd image store is required; infra must configure the engine before building or deploying",
            )
            self._store_checked = True

    def tool(self, *command, frontend=False, env=None):
        image = self.node_image if frontend else self.tool_image
        info = self.inspect_image(image, self.architecture())
        require(
            (info["Config"].get("Labels") or {}).get("io.scaffold.tooling-inputs")
            == self.tooling_inputs(frontend),
            "tooling inputs changed; run make setup",
        )
        options = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--init",
            "--volume",
            f"{self.root}:/workspace",
            "--workdir",
            "/workspace/frontend" if frontend else "/workspace/backend",
            "--env",
            "HOME=/tmp",
            "--env",
            "REPO_DIR=/workspace",
            "--env",
            "DJANGO_SECRET_KEY=source-check-only-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789",
            "--env",
            "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1",
            "--env",
            "DATABASE_URL=sqlite:////tmp/scaffold-source-check.sqlite3",
            "--env",
            "DJANGO_SETTINGS_MODULE=app.project.settings.dev",
        ]
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        if not common.is_relative_to(self.root):
            options += ["--volume", f"{common}:{common}:ro"]
        if frontend:
            options += [
                "--volume",
                f"{self.local}/tooling/frontend/node_modules:/workspace/frontend/node_modules",
            ]
        for key, value in (env or {}).items():
            options += ["--env", key + "=" + value]
        self.execute(*options, image, *command)

    def tooling_inputs(self, frontend=False):
        files = ["deploy/dependencies.json"]
        if frontend:
            files += [
                "deploy/frontend-tooling.Dockerfile",
                "frontend/package.json",
                "frontend/pnpm-lock.yaml",
            ]
        else:
            files += [
                "deploy/tooling.Dockerfile",
                self.pyproject,
                str(Path(self.pyproject).with_name("uv.lock")),
            ]
        digest = hashlib.sha256(b"containerd-single-platform-v1")
        for name in files:
            digest.update(name.encode() + b"\0" + (self.root / name).read_bytes())
        return digest.hexdigest()

    def prepared_tool(self, image, expected):
        try:
            info = self.inspect_image(image, self.architecture())
        except Rejected:
            return False
        return (info["Config"].get("Labels") or {}).get("io.scaffold.tooling-inputs") == expected

    def inspect_image(self, reference, platform=None):
        immutable(reference) if reference.startswith("sha256:") or "@" in reference else None
        data = json.loads(self.execute("docker", "image", "inspect", reference, capture=True))[0]
        actual = data["Os"] + "/" + data["Architecture"]
        require(not platform or actual == platform, "image architecture mismatch")
        immutable(data["Id"])
        return data

    def build_inputs(self):
        pins = read_json(self.root / "deploy/dependencies.json")["images"]
        result = []
        for argument, name in (
            ("UV_IMAGE", "uv"),
            ("PYTHON_IMAGE", "python"),
            ("NODE_IMAGE", "node"),
            (
                "NGINX_IMAGE",
                "nginx-django" if self.profile == "server-rendered-django" else "nginx",
            ),
        ):
            dependency = pins[name]
            value = immutable(dependency["pin"])
            require(
                value.startswith(dependency["source"] + "@"),
                "image source declaration changed; run make deps-lock",
            )
            result += ["--build-arg", argument + "=" + value]
        return result

    def gate(self, operation):
        project = "/workspace/" + self.pyproject
        commands = {
            "format-check": [
                ("ruff", "format", "--config", project, "--check", ".", *self.script_sources())
            ],
            "lint": [
                ("python", "scripts/check_no_any.py"),
                ("ruff", "check", "--config", project, ".", *self.script_sources()),
            ],
            "typecheck": [("mypy", "--config-file", project, ".")],
            "migrations-check": [
                ("python", "-m", "app.manage", "makemigrations", "--check", "--dry-run")
            ],
            "framework-check": [("python", "-m", "app.manage", "check", "--deploy")],
            "migrations": [("python", "-m", "app.manage", "makemigrations")],
            "format": [
                ("ruff", "check", "--config", project, "--fix", ".", *self.script_sources()),
                ("ruff", "format", "--config", project, ".", *self.script_sources()),
            ],
        }
        for command in commands.get(operation, []):
            env = (
                {
                    "DJANGO_SETTINGS_MODULE": "app.project.settings.prod",
                    "DATABASE_URL": "postgresql://check:check@unused/check",
                }
                if operation == "framework-check"
                else None
            )
            self.tool(*command, env=env)
        # Keep the existing archive library's root Ruff style; application
        # configuration continues to govern the selected application's checks.
        archive = "/workspace/scripts/release_bundle.py"
        shared = {
            "format-check": [
                ("ruff", "format", "--isolated", "--line-length", "88", "--check", archive)
            ],
            "lint": [("ruff", "check", "--isolated", archive)],
            "format": [
                ("ruff", "check", "--isolated", "--fix", archive),
                ("ruff", "format", "--isolated", "--line-length", "88", archive),
            ],
        }
        for command in shared.get(operation, []):
            self.tool(*command)
        if operation == "test":
            coverage = os.environ.get("COVERAGE", "0") == "1"
            command = ["pytest", "-c", project, "."]
            if coverage:
                command += [
                    "--cov=src/app",
                    "--cov-config=" + project,
                    "--cov-report=term-missing",
                    "--cov-report=xml",
                ]
            self.tool(*command)
        if self.profile == "react-vite" and operation in (
            "format-check",
            "lint",
            "typecheck",
            "test",
            "format",
        ):
            command = [
                "pnpm",
                "run",
                "fmt-check" if operation == "format-check" else operation,
            ]
            if operation == "test" and os.environ.get("COVERAGE") == "1":
                command += [
                    "--coverage",
                    "--coverage.all=true",
                    "--coverage.include=src/**/*.{ts,tsx}",
                    "--coverage.exclude=src/**/__tests__/**",
                    "--coverage.exclude=src/**/*.{test,spec}.{ts,tsx}",
                    "--coverage.exclude=src/setupTests.ts",
                    "--coverage.exclude=src/**/*.d.ts",
                    "--coverage.reporter=lcov",
                    "--coverage.reporter=json-summary",
                ]
            self.tool(*command, frontend=True)
        if operation == "test" and os.environ.get("COVERAGE") == "1":
            self.changed_coverage()

    def changed_coverage(self):
        # Uses the same coverage report and an explicit Git diff base; no second test run.
        self.execute(
            sys.executable,
            self.root / "scripts/changed_coverage.py",
            "--root",
            self.root,
            "--base",
            os.environ.get("DIFF_BASE") or "origin/main",
            "--xml",
            self.root / "backend/coverage.xml",
        )

    def check(self):
        for name in (
            "format-check",
            "lint",
            "typecheck",
            "migrations-check",
            "framework-check",
            "test",
        ):
            previous = os.environ.get("COVERAGE")
            try:
                if name == "test":
                    os.environ["COVERAGE"] = "1"
                self.gate(name)
            finally:
                if previous is None:
                    os.environ.pop("COVERAGE", None)
                else:
                    os.environ["COVERAGE"] = previous
        evidence = self.local / "checks" / (self.head() + ".json")
        if not self.git("status", "--porcelain"):
            write_json(
                evidence,
                {
                    "revision": self.head(),
                    "profile": self.profile,
                    "passed_at": time.time(),
                    "source": "local-checks",
                },
            )
        else:
            evidence.unlink(missing_ok=True)
            print(
                "Source checks passed; commit changes and rerun checks before building a release."
            )

    def setup(self, release=None):
        if release and "tooling" in release["images"]:
            self.execute(
                "docker",
                "tag",
                release.get("image_ids", release["images"])["tooling"],
                self.tool_image,
            )
        elif not self.prepared_tool(self.tool_image, self.tooling_inputs()):
            self.execute(
                "docker",
                "build",
                "--provenance=false",
                "--platform",
                self.architecture(),
                *self.build_inputs(),
                "--label",
                "io.scaffold.tooling-inputs=" + self.tooling_inputs(),
                "--file",
                "deploy/tooling.Dockerfile",
                "--build-arg",
                "PROFILE=" + self.profile,
                "--tag",
                self.tool_image,
                ".",
                cwd=self.root,
            )
        # Only generated check outputs are repaired; source, secrets and app data
        # retain their existing ownership. Isolated Python loads no project code.
        self.execute(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            f"{self.root}:/workspace",
            self.tool_image,
            "python",
            "-I",
            "-S",
            "-c",
            GENERATED_OWNERSHIP,
            str(os.getuid()),
            str(os.getgid()),
        )
        if self.profile == "react-vite":
            if release and "frontend-tooling" in release["images"]:
                self.execute(
                    "docker",
                    "tag",
                    release.get("image_ids", release["images"])["frontend-tooling"],
                    self.node_image,
                )
            elif not self.prepared_tool(self.node_image, self.tooling_inputs(True)):
                self.execute(
                    "docker",
                    "build",
                    "--provenance=false",
                    "--platform",
                    self.architecture(),
                    *self.build_inputs(),
                    "--label",
                    "io.scaffold.tooling-inputs=" + self.tooling_inputs(True),
                    "--file",
                    "deploy/frontend-tooling.Dockerfile",
                    "--tag",
                    self.node_image,
                    ".",
                    cwd=self.root,
                )
            cache = self.local / "tooling/frontend"
            cache.mkdir(parents=True, exist_ok=True)
            # This directory contains only regenerated container dependencies.
            if (cache / "node_modules").exists():
                self.execute(
                    "docker",
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network",
                    "none",
                    "--user",
                    "0:0",
                    "--volume",
                    f"{cache}:/cache",
                    self.node_image,
                    "rm",
                    "-rf",
                    "/cache/node_modules",
                )
            self.execute(
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--volume",
                f"{cache}:/cache",
                self.node_image,
                "cp",
                "-a",
                "--no-preserve=ownership",
                "/opt/frontend/node_modules",
                "/cache/node_modules",
            )

    def hooks(self):
        directory = self.local / "hooks"
        directory.mkdir(parents=True, exist_ok=True)
        atomic(
            directory / "pre-commit",
            "#!/bin/sh\nexec make --no-print-directory check\n",
        )
        (directory / "pre-commit").chmod(0o700)
        previous = self.git("config", "--local", "--default", "", "--get", "core.hooksPath")
        self.git("config", "extensions.worktreeConfig", "true")
        self.git("config", "--worktree", "core.hooksPath", str(directory))
        if previous == str(directory):
            self.git("config", "--local", "--unset", "core.hooksPath")

    def execute(self, *arguments, **options):
        return run(*arguments, **options)

    def script_sources(self):
        return tuple(
            "/workspace/scripts/" + name
            for name in SCRIPT_NAMES
            if (self.root / "scripts" / name).is_file()
        )

    def dispatch(self, operation):
        require(operation in ("setup", "hooks", "check", *GATES), "unknown source command")
        if operation in GATES:
            self.gate(operation)
        else:
            getattr(self, operation)()


def main():
    try:
        require(len(sys.argv) == 2, "invoke through a documented Make target")
        SourceOperator().dispatch(sys.argv[1])
    except (Rejected, OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
