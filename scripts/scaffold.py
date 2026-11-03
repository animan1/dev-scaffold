"""Implement the public Make commands using the tenant's existing Compose services.

Python is host-side, standard-library-only orchestration. Application runtimes,
dependency resolution and source checks run in Docker.
"""

import datetime
import errno
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from profile_metadata import metadata
from scaffold_support import (
    Rejected,
    atomic,
    compose_literals,
    immutable,
    locked,
    read_env,
    read_json,
    release_identity,
    require,
    revision,
    run,
    save_images,
    seal_release,
    target,
    validate_release,
    verify_seal,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCES = (
    "/workspace/scripts/scaffold.py",
    "/workspace/scripts/scaffold_support.py",
    "/workspace/scripts/profile_metadata.py",
    "/workspace/scripts/changed_coverage.py",
)


class Operator:
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

    def git(self, *arguments):
        return run("git", *arguments, cwd=self.root, capture=True)

    def head(self):
        return revision(self.git("rev-parse", "HEAD"))

    def clean_source(self):
        require(
            not self.git("status", "--porcelain"),
            "commit all source changes before building a release",
        )
        return self.head()

    def architecture(self):
        if self._platform:
            return self._platform
        value = os.environ.get("RELEASE_PLATFORM")
        if not value:
            value = run(
                "docker",
                "version",
                "--format",
                "{{.Server.Os}}/{{.Server.Arch}}",
                capture=True,
            )
        require(
            value in ("linux/amd64", "linux/arm64"),
            "unsupported destination architecture",
        )
        self._platform = value
        return value

    def release_dir(self, rev=None):
        return (
            self.local
            / "releases"
            / (rev or self.head())
            / self.profile
            / self.architecture().split("/")[1]
        )

    def release(self, rev=None):
        data = validate_release(
            read_json(self.release_dir(rev) / "manifest.json"),
            self.profile,
            self.architecture(),
        )
        require(data["revision"] == (rev or self.head()), "release directory revision mismatch")
        if os.environ.get("RELEASE_IDENTITY"):
            require(
                release_identity(data) == os.environ["RELEASE_IDENTITY"],
                "release image set differs from the selected backup",
            )
        return data

    def inputs(self, environment):
        if environment == "prod" and os.environ.get("PROD_ENV_FILE"):
            return Path(os.environ["PROD_ENV_FILE"]).resolve()
        if environment == "prod" and (self.root / "deploy/.env.prod").exists():
            return self.root / "deploy/.env.prod"
        return self.local / "env" / f"{environment}.env"

    def settings(self, environment):
        values = read_env(self.inputs(environment))
        require(
            all(
                values.get(key)
                for key in (
                    "DJANGO_SECRET_KEY",
                    "POSTGRES_USER",
                    "POSTGRES_PASSWORD",
                    "POSTGRES_DB",
                    "BACKUP_PASSWORD",
                )
            ),
            "incomplete private environment inputs",
        )
        url = values.get("DATABASE_URL", "")
        for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            url = url.replace("${" + key + "}", urllib.parse.quote(values[key], safe=""))
        parsed = urllib.parse.urlsplit(url)
        require(
            parsed.scheme in ("postgres", "postgresql")
            and parsed.hostname == "db"
            and parsed.port in (None, 5432)
            and urllib.parse.unquote(parsed.username or "") == values["POSTGRES_USER"]
            and urllib.parse.unquote(parsed.password or "") == values["POSTGRES_PASSWORD"]
            and urllib.parse.unquote(parsed.path.removeprefix("/")) == values["POSTGRES_DB"],
            "DATABASE_URL must match the configured database service and credentials",
        )
        values["DATABASE_URL"] = url
        return values

    def state_dir(self, environment):
        settings = self.settings(environment)
        base = Path(
            os.environ.get("DEPLOYMENT_STATE_DIR") or settings.get("DEPLOYMENT_STATE_DIR", "")
        )
        require(
            base.is_absolute() and not base.resolve().is_relative_to(self.root.resolve()),
            "DEPLOYMENT_STATE_DIR must be absolute host storage outside the checkout",
        )
        return base / self.project / environment

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
        run(*options, image, *command)

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
        digest = hashlib.sha256()
        for name in files:
            digest.update(name.encode() + b"\0" + (self.root / name).read_bytes())
        return digest.hexdigest()

    def prepared_tool(self, image, expected):
        try:
            info = self.inspect_image(image, self.architecture())
        except Rejected:
            return False
        return (info["Config"].get("Labels") or {}).get("io.scaffold.tooling-inputs") == expected

    def backup_inputs(self):
        digest = hashlib.sha256()
        for name in (
            "profiles/immutable-backup/Dockerfile",
            "profiles/immutable-backup/backup.sh",
            "deploy/dependencies.json",
        ):
            digest.update((self.root / name).read_bytes())
        return digest.hexdigest()

    def setup(self):
        cached = self.release_dir() / "manifest.json"
        release = self.release() if cached.exists() else None
        if release and "tooling" in release["images"]:
            run(
                "docker",
                "tag",
                release.get("image_ids", release["images"])["tooling"],
                self.tool_image,
            )
        elif not self.prepared_tool(self.tool_image, self.tooling_inputs()):
            run(
                "docker",
                "build",
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
        if self.profile == "react-vite":
            if release and "frontend-tooling" in release["images"]:
                run(
                    "docker",
                    "tag",
                    release.get("image_ids", release["images"])["frontend-tooling"],
                    self.node_image,
                )
            elif not self.prepared_tool(self.node_image, self.tooling_inputs(True)):
                run(
                    "docker",
                    "build",
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
                shutil.rmtree(cache / "node_modules")
            run(
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--volume",
                f"{cache}:/cache",
                self.node_image,
                "cp",
                "-a",
                "/opt/frontend/node_modules",
                "/cache/node_modules",
            )
        if release:
            run(
                "docker",
                "tag",
                release.get("image_ids", release["images"])["backup"],
                f"local/{self.project}-backup:discovery",
            )
        elif self.prepared_tool(f"local/{self.project}-backup:discovery", self.backup_inputs()):
            run(
                "docker",
                "tag",
                f"local/{self.project}-backup:discovery",
                f"local/{self.project}-backup:{self.head()}",
            )
        else:
            pins = read_json(self.root / "deploy/dependencies.json")["images"]
            options = []
            for name in ("postgres", "restic", "rclone"):
                options += [
                    "--build-arg",
                    name.upper() + "_IMAGE=" + immutable(pins[name]["pin"]),
                ]
            run(
                "docker",
                "build",
                "--platform",
                self.architecture(),
                *options,
                "--label",
                "io.scaffold.tooling-inputs=" + self.backup_inputs(),
                "--file",
                "profiles/immutable-backup/Dockerfile",
                "--tag",
                f"local/{self.project}-backup:{self.head()}",
                "--tag",
                f"local/{self.project}-backup:discovery",
                ".",
                cwd=self.root,
            )

    def prepare(self, environment):
        path = self.inputs(environment)
        existing = read_env(path) if path.exists() else {}
        password = existing.get("POSTGRES_PASSWORD", secrets.token_hex(24))
        state = Path.home() / ".local/state/dev-scaffold"
        values = {
            "DJANGO_SECRET_KEY": secrets.token_hex(40),
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://localhost",
            "POSTGRES_USER": "app",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": "app",
            "DATABASE_URL": f"postgresql://app:{password}@db:5432/app",
            "DEPLOYMENT_STATE_DIR": str(state),
            "BACKUP_PASSWORD": secrets.token_hex(32),
            "BACKUP_HOST": self.project + "-" + environment,
            "HOST_INGRESS": "1",
            "HOST_INGRESS_HOST": "localhost",
            "APP_PORT": "8080"
            if environment == "dev"
            else "18080"
            if environment == "prod"
            else "18081",
        }
        template = self.root / f"deploy/env/{environment}.env.example"
        if template.exists():
            values.update(read_env(template))
        if "DATABASE_URL" not in existing:
            effective = {**values, **existing}
            values["DATABASE_URL"] = (
                "postgresql://"
                + urllib.parse.quote(effective["POSTGRES_USER"], safe="")
                + ":"
                + urllib.parse.quote(effective["POSTGRES_PASSWORD"], safe="")
                + "@db:5432/"
                + urllib.parse.quote(effective["POSTGRES_DB"], safe="")
            )
        if existing:
            additions = {key: value for key, value in values.items() if key not in existing}
            if additions:
                atomic(
                    path,
                    path.read_text().rstrip()
                    + "\n"
                    + "".join(f"{key}={value}\n" for key, value in additions.items()),
                )
        else:
            atomic(path, "".join(f"{key}={value}\n" for key, value in values.items()))
        settings = self.settings(environment)
        require(settings.get("BACKUP_PASSWORD"), "BACKUP_PASSWORD must be configured")
        require(
            settings.get("HOST_INGRESS", "1") == "1",
            "configure host ingress before deployment",
        )
        self.state_dir(environment).mkdir(parents=True, exist_ok=True, mode=0o700)
        Path(settings.get("BACKUP_LOCAL_PATH", str(self.state_dir(environment) / "backups"))).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
        print(f"Prepared private {environment} inputs: {path}")

    def inspect_image(self, reference, platform=None):
        immutable(reference) if reference.startswith("sha256:") or "@" in reference else None
        data = json.loads(run("docker", "image", "inspect", reference, capture=True))[0]
        actual = data["Os"] + "/" + data["Architecture"]
        require(not platform or actual == platform, "image architecture mismatch")
        immutable(data["Id"])
        return data

    def config(self, environment, release=None):  # noqa: C901 -- normalize existing profile Compose contracts
        settings = self.settings(environment)
        rev = self.head()
        if environment != "dev":
            release = release or self.release()
            require(
                release["revision"] == rev,
                "checkout and release manifest revision mismatch",
            )
        files = (
            ["deploy/docker-compose.dev.yml"]
            if self.profile == "react-vite" and environment == "dev"
            else ["profiles/server-rendered-django/compose.yml"]
            if environment == "dev"
            else [
                "deploy/docker-compose.prod.yml",
                "deploy/docker-compose.release.yml",
                "deploy/docker-compose.host-ingress.yml",
            ]
            if self.profile == "react-vite"
            else ["profiles/server-rendered-django/release.compose.yml"]
        )
        runtime = {
            **os.environ,
            **settings,
            "COMPOSE_PROJECT_NAME": self.project + "-" + environment,
            "PROD_ENV_FILE": str(self.inputs(environment)),
            "GIT_COMMON_DIR": self.git("rev-parse", "--path-format=absolute", "--git-common-dir"),
            "HOST_UID": str(os.getuid()),
            "HOST_GID": str(os.getgid()),
            "APP_HOST": settings.get("HOST_INGRESS_HOST", "localhost"),
            "APP_PORT": settings.get("APP_PORT", "8080"),
            "HOST_INGRESS_PORT": settings.get("APP_PORT", "18080"),
            "RELEASE_HTTP_PORT": settings.get("APP_PORT", "18080"),
            "RELEASE_REVISION": rev,
            "RELEASE_IMAGE_PREFIX": (release or {}).get("prefix", self.prefix),
            "RELEASE_BACKEND_IMAGE": (release or {})
            .get("images", {})
            .get("backend", self.tool_image),
            "RELEASE_WEB_IMAGE": (release or {}).get("images", {}).get("web", self.node_image),
            "BACKUP_IMAGE": (release or {})
            .get("image_ids", (release or {}).get("images", {}))
            .get("backup", f"local/{self.project}-backup:{self.head()}"),
            "BACKUP_LOCAL_PATH": settings.get(
                "BACKUP_LOCAL_PATH", str(self.state_dir(environment) / "backups")
            ),
            "BACKUP_RCLONE_CONFIG_DIR": settings.get(
                "BACKUP_RCLONE_CONFIG_DIR", str(self.state_dir(environment) / "rclone")
            ),
        }
        files.append("profiles/immutable-backup/compose.yml")
        if (
            os.environ.get("MONITORING") == "1"
            and settings.get("MONITOR_NOTIFICATION_BACKEND") == "external"
        ):
            files.append("deploy/docker-compose.external-monitoring.yml")
        base_directory = self.root / "deploy" if self.profile == "react-vite" else self.root
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(base_directory),
            "--env-file",
            str(self.inputs(environment)),
        ]
        for file in files:
            command += ["-f", str(self.root / file)]
        model = json.loads(
            run(
                *command,
                "--profile",
                "*",
                "config",
                "--format",
                "json",
                env=runtime,
                capture=True,
            )
        )
        model["name"] = self.project + "-" + environment
        services = model["services"]
        services.pop("backup-init", None)
        for name, service in services.items():
            require(
                not service.get("container_name"),
                "container names must remain owned by the Compose project",
            )
            service.pop("profiles", None)
            if environment != "dev":
                service.pop("build", None)
                service["pull_policy"] = "never"
                service["platform"] = release["platform"]
                role = (
                    "backend"
                    if name in (self.app, "monitor")
                    else "web"
                    if name == self.web
                    else name
                )
                identities = release.get("image_ids", release["images"])
                require(
                    role in identities,
                    "missing captured image for configured service: " + name,
                )
                service["image"] = identities[role]
            elif name in (self.app, "monitor"):
                service.pop("build", None)
                service["image"] = self.tool_image
                service["working_dir"] = "/workspace/backend"
                shared_status = [
                    value
                    for value in service.get("volumes", [])
                    if value.get("target") == "/backup-status"
                ]
                service["volumes"] = [
                    {"type": "bind", "source": str(self.root), "target": "/workspace"},
                    *shared_status,
                ]
                service["command"] = (
                    ["python", "-m", "app.manage", "runserver", "0.0.0.0:8000"]
                    if name == self.app
                    else [
                        "python",
                        "-m",
                        "app.manage",
                        "monitor_operational_integrity",
                        "--watch",
                    ]
                )
                service.pop("ports", None)
            elif name == "frontend":
                service["image"] = self.node_image
                service["working_dir"] = "/workspace/frontend"
                service["command"] = ["pnpm", "dev", "--host", "0.0.0.0"]
                service["volumes"] = [
                    {"type": "bind", "source": str(self.root), "target": "/workspace"},
                    {
                        "type": "bind",
                        "source": str(self.local / "tooling/frontend/node_modules"),
                        "target": "/workspace/frontend/node_modules",
                    },
                ]
                service.pop("ports", None)
                service["healthcheck"] = {
                    "test": [
                        "CMD",
                        "node",
                        "-e",
                        "fetch('http://localhost:5173').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
                    ],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                }
            if name in (self.app, "monitor"):
                service.setdefault("environment", {}).update(
                    {
                        **settings,
                        "DJANGO_SETTINGS_MODULE": "app.project.settings.dev"
                        if environment == "dev"
                        else "app.project.settings.prod",
                        "RELEASE_REVISION": rev,
                        "DEPLOYMENT_ENVIRONMENT": "development"
                        if environment == "dev"
                        else "production",
                    }
                )
            service.pop("env_file", None)
        if "db" not in services:
            services["db"] = {
                "image": (release or {})
                .get("images", {})
                .get(
                    "db",
                    read_json(self.root / "deploy/dependencies.json")["images"]["postgres"]["pin"],
                ),
                "environment": {
                    key: settings[key]
                    for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
                },
                "volumes": [
                    {
                        "type": "volume",
                        "source": "pgdata",
                        "target": "/var/lib/postgresql/data",
                    }
                ],
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                    ],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                },
            }
            model.setdefault("volumes", {})["pgdata"] = {}
        services["db"]["environment"] = {
            key: settings[key] for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
        }
        if environment == "dev":
            services[self.app].setdefault("depends_on", {})["db"] = {
                "condition": "service_healthy",
                "required": True,
            }
            services[self.app]["healthcheck"] = {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/healthz')",
                ],
                "interval": "2s",
                "timeout": "3s",
                "retries": 30,
            }
            if self.profile == "react-vite":
                services[self.web].pop("build", None)
                services[self.web]["image"] = "nginx:1.27"
                services[self.web]["ports"] = [
                    {
                        "target": 80,
                        "published": settings.get("APP_PORT", "8080"),
                        "host_ip": "127.0.0.1",
                        "protocol": "tcp",
                    }
                ]
                services[self.web].setdefault("depends_on", {})["frontend"] = {
                    "condition": "service_healthy",
                    "required": True,
                }
            else:
                services[self.app]["ports"] = [
                    {
                        "target": 8000,
                        "published": settings.get("APP_PORT", "8080"),
                        "host_ip": "127.0.0.1",
                        "protocol": "tcp",
                    }
                ]
        self.add_backup(model, environment, settings, release)
        if environment == "ephemeral":
            # Writable host paths must never let disposable validation touch
            # persistent configuration, repositories or application data.
            for name, spec in services.items():
                for mount in spec.get("volumes", []):
                    if mount["type"] == "bind" and not mount.get("read_only"):
                        key = (
                            "disposable_"
                            + hashlib.sha256((name + mount["target"]).encode()).hexdigest()[:12]
                        )
                        destination = mount["target"]
                        mount.clear()
                        mount.update(type="volume", source=key, target=destination)
                        model.setdefault("volumes", {})[key] = {}
            services["backup"]["environment"]["RESTIC_REPOSITORY"] = "/backups"
        if os.environ.get("MONITORING", "0") != "1":
            services.pop("monitor", None)
        # All generated volumes remain inside the environment's sole namespace.
        for name, volume in model.get("volumes", {}).items():
            require(
                not volume.get("external"),
                "external volumes require an explicit tenant adapter",
            )
            volume["name"] = model["name"] + "_" + name
        path = self.local / "config" / f"{environment}.json"
        write_json(path, model)
        return path, model

    def add_backup(self, model, environment, settings, release):
        # Keep the existing profile's provider, schedule, user and volume contract.
        # Even without its unattended worker, migrations use the same backup tool.
        service = model["services"]["backup"]
        service["pull_policy"] = "never"
        service.setdefault("depends_on", {})["db"] = {
            "condition": "service_healthy",
            "required": True,
        }
        service["environment"].update(
            {
                "BACKUP_RELEASE_REQUIRED": "0" if environment == "dev" else "1",
                "BACKUP_INITIAL_REVISION": self.head(),
                "BACKUP_INITIAL_IDENTITY": release_identity(release) if release else "",
            }
        )

    def compose(self, environment, *arguments, release=None, capture=False):
        _, model = self.config(environment, release)
        return self.compose_model(environment, model, *arguments, capture=capture)

    def compose_model(self, environment, model, *arguments, capture=False, current=False):
        path = self.local / "compose" / (environment + ("-current" if current else "") + ".json")
        write_json(path, compose_literals(model))
        private = [
            value
            for key, value in self.settings(environment).items()
            if any(word in key for word in ("PASSWORD", "SECRET", "TOKEN", "DATABASE_URL"))
        ]
        private += [
            value
            for service in model["services"].values()
            for key, value in service.get("environment", {}).items()
            if any(
                word in key
                for word in (
                    "PASSWORD",
                    "SECRET",
                    "TOKEN",
                    "CREDENTIAL",
                    "DATABASE_URL",
                    "REPOSITORY",
                )
            )
        ]
        return run(
            "docker",
            "compose",
            "--project-directory",
            self.root,
            "--file",
            path,
            *arguments,
            capture=capture,
            sensitive=private,
        )

    def current_model(self, environment):
        path = self.state_dir(environment) / "runtime.json"
        if path.exists():
            model = read_json(path)
            require(
                model.get("name") == self.project + "-" + environment,
                "recorded runtime namespace mismatch",
            )
            return path, model
        return self.config(environment)

    def current_compose(self, environment, *arguments, capture=False):
        _, model = self.current_model(environment)
        return self.compose_model(environment, model, *arguments, capture=capture, current=True)

    def require_dependencies(self, environment, service, model):
        for dependency in model["services"][service].get("depends_on", {}):
            raw = self.current_compose(
                environment, "ps", "--format", "json", dependency, capture=True
            )
            require(raw, "required service dependency is not running")
            rows = (
                json.loads(raw)
                if raw.lstrip().startswith("[")
                else [json.loads(line) for line in raw.splitlines()]
            )
            require(
                any(
                    row.get("Service") == dependency
                    and row.get("State") == "running"
                    and row.get("Health", "") in ("", "healthy")
                    for row in rows
                ),
                "required service dependency is not ready",
            )

    def preflight_ports(self, environment, model, service=None):
        selected = {service: model["services"][service]} if service else model["services"]
        owned = None
        for name, spec in selected.items():
            for port in spec.get("ports", []):
                if port.get("protocol", "tcp") != "tcp":
                    continue
                number = int(port["published"])
                require(0 < number < 65536, "invalid published port")
                address = port.get("host_ip", "127.0.0.1")
                try:
                    with socket.socket(
                        socket.AF_INET6 if ":" in address else socket.AF_INET,
                        socket.SOCK_STREAM,
                    ) as probe:
                        probe.bind((address, number))
                except OSError as error:
                    if error.errno != errno.EADDRINUSE:
                        continue
                    if owned is None:
                        raw = self.current_compose(
                            environment, "ps", "--format", "json", capture=True
                        )
                        owned = (
                            json.loads(raw)
                            if raw.lstrip().startswith("[")
                            else [json.loads(line) for line in raw.splitlines() if line]
                        )
                    require(
                        any(
                            row.get("Service") == name
                            and any(
                                item.get("PublishedPort") == number
                                for item in row.get("Publishers") or []
                            )
                            for row in owned
                        ),
                        f"port {number} is occupied outside this environment; deployment unchanged",
                    )

    def manage(self, environment, *arguments, release=None, capture=False):
        return self.compose(
            environment,
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "--entrypoint",
            "python",
            self.app,
            "-m",
            "app.manage",
            *arguments,
            release=release,
            capture=capture,
        )

    def gate(self, operation):
        project = "/workspace/" + self.pyproject
        commands = {
            "format-check": [
                ("ruff", "format", "--config", project, "--check", ".", *SCRIPT_SOURCES)
            ],
            "lint": [
                ("python", "scripts/check_no_any.py"),
                ("ruff", "check", "--config", project, ".", *SCRIPT_SOURCES),
            ],
            "typecheck": [("mypy", "--config-file", project, ".")],
            "migrations-check": [
                ("python", "-m", "app.manage", "makemigrations", "--check", "--dry-run")
            ],
            "framework-check": [("python", "-m", "app.manage", "check", "--deploy")],
            "migrations": [("python", "-m", "app.manage", "makemigrations")],
            "format": [
                ("ruff", "check", "--config", project, "--fix", ".", *SCRIPT_SOURCES),
                ("ruff", "format", "--config", project, ".", *SCRIPT_SOURCES),
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
        run(
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

    def build(self):
        rev = self.clean_source()
        platform = self.architecture()
        directory = self.release_dir(rev)
        if (directory / "manifest.json").exists() and (directory / "integrity.json").exists():
            self.release(rev)
            verify_seal(directory)
            evidence = self.local / "checks" / (rev + ".json")
            if evidence.exists():
                check = read_json(evidence)
                require(
                    check.get("profile") == self.profile,
                    "source validation belongs to another profile",
                )
                write_json(directory / "source-check.json", check)
                seal_release(directory)
            print(f"Reusing immutable release {rev} for {platform}")
            return
        directory.mkdir(parents=True, exist_ok=True)
        source = subprocess.check_output(["git", "archive", "--format=tar", rev], cwd=self.root)
        with (
            locked(directory),
            tempfile.TemporaryDirectory(dir=self.local, prefix="build-") as temporary,
        ):
            context = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(source)) as archive:
                archive.extractall(context, filter="data")
            self.build_images(rev, platform, directory, context, source)

    def build_images(self, rev, platform, directory, source_root, source):
        common = [
            "--platform",
            platform,
            "--label",
            "org.opencontainers.image.revision=" + rev,
        ]
        if self.profile == "react-vite":
            builds = [
                ("backend", "backend/Dockerfile", "prod", "backend"),
                ("web", "deploy/nginx/Dockerfile", "runtime", "."),
            ]
        else:
            builds = [
                (
                    "backend",
                    "profiles/server-rendered-django/backend.Dockerfile",
                    "production",
                    ".",
                ),
                (
                    "web",
                    "profiles/server-rendered-django/release-nginx.Dockerfile",
                    "runtime",
                    ".",
                ),
            ]
        images = {}
        for role, dockerfile, stage, context in builds:
            tag = f"{self.prefix}-{role}:{rev}"
            run(
                "docker",
                "build",
                *common,
                *self.build_inputs(),
                "--file",
                dockerfile,
                "--target",
                stage,
                "--tag",
                tag,
                context,
                cwd=source_root,
            )
            images[role] = self.inspect_image(tag, platform)["Id"]
        pins = read_json(self.root / "deploy/dependencies.json")["images"]
        tag = f"{self.prefix}-backup:{rev}"
        options = []
        for name in ("postgres", "restic", "rclone"):
            options += [
                "--build-arg",
                name.upper() + "_IMAGE=" + immutable(pins[name]["pin"]),
            ]
        run(
            "docker",
            "build",
            *common,
            *options,
            "--file",
            "profiles/immutable-backup/Dockerfile",
            "--tag",
            tag,
            ".",
            cwd=source_root,
        )
        images["backup"] = self.inspect_image(tag, platform)["Id"]
        images["tooling"] = self.inspect_image(self.tool_image, platform)["Id"]
        if self.profile == "react-vite":
            images["frontend-tooling"] = self.inspect_image(self.node_image, platform)["Id"]
        runtime = read_json(self.root / "deploy/dependencies.json").get(
            "runtime", {"db": "postgres"}
        )
        for service, dependency in runtime.items():
            require(
                re.fullmatch("[a-z][a-z0-9_-]*", service),
                "invalid runtime service name",
            )
            if dependency in images:
                images[service] = images[dependency]
            else:
                reference = immutable(pins[dependency]["pin"])
                try:
                    info = self.inspect_image(reference, platform)
                except Rejected:
                    run("docker", "pull", "--platform", platform, reference)
                    info = self.inspect_image(reference, platform)
                images[service] = info["Id"]
        manifest = {
            "schema": 1,
            "revision": rev,
            "profile": self.profile,
            "backup_profile": self.backup_profile,
            "platform": platform,
            "prefix": self.prefix,
            "images": images,
        }
        validate_release(manifest)
        (directory / "source.tar").write_bytes(source)
        run("git", "bundle", "create", directory / "source.bundle", "HEAD", cwd=self.root)
        write_json(directory / "manifest.json", manifest)
        evidence = self.local / "checks" / (rev + ".json")
        if evidence.exists():
            source_check = read_json(evidence)
            require(
                source_check.get("profile") == self.profile,
                "source validation belongs to another profile",
            )
            write_json(directory / "source-check.json", source_check)
        save_images(directory / "images.tar.gz", images.values())
        seal_release(directory)
        print(f"Recorded immutable release {rev} for {platform}")

    def image_check(self):
        release = self.release()
        verify_seal(self.release_dir())
        for role, reference in release.get("image_ids", release["images"]).items():
            info = self.inspect_image(reference, release["platform"])
            if role in ("backend", "web", "backup"):
                require(
                    (info["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
                    == release["revision"],
                    "image revision label mismatch",
                )
            if self.profile == "server-rendered-django" and role in ("backend", "web"):
                require(
                    info["Config"].get("User") not in (None, "", "0", "root", "0:0"),
                    "application image must be non-root",
                )
        run(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--entrypoint",
            "sh",
            release.get("image_ids", release["images"])["backup"],
            "-c",
            "test $(id -u) = 10001 && pg_dump --version && restic version && rclone version && jq --version && test -x /usr/local/bin/scaffold-backup && test -s /etc/ssl/certs/ca-certificates.crt",
        )
        write_json(
            self.release_dir() / "image-check.json",
            {
                "revision": release["revision"],
                "platform": release["platform"],
                "images": release["images"],
                "passed_at": time.time(),
                "source": "local-image-check",
            },
        )
        seal_release(self.release_dir())

    def backup(  # noqa: C901 -- snapshot operations share one private helper model
        self,
        environment,
        operation="once",
        snapshot=None,
        capture=False,
        protect=False,
        source=None,
    ):
        release = None if environment == "dev" else self.release()
        # The backup image owns its repository and shared status volume; app data
        # and credentials are neither replaced nor exposed on the command line.
        _, model = self.config(environment, release)
        if source and source != environment:
            require(
                source in ("dev", "prod", "ephemeral"),
                "unknown backup source environment",
            )
            require(
                operation in ("snapshot-exists", "restore"),
                "cross-environment backup access is read-only",
            )
            _, source_model = self.current_model(source)
            source_service = source_model["services"]["backup"]
            service = model["services"]["backup"]
            for key, value in source_service["environment"].items():
                if key.startswith(("RESTIC_", "RCLONE_")) or key in (
                    "BACKUP_HOST",
                    "BACKUP_TAG",
                    "BACKUP_FILENAME",
                ):
                    service["environment"][key] = value
            service["volumes"] = [
                mount for mount in service["volumes"] if mount["target"] == "/backup-status"
            ]
            for mount in source_service["volumes"]:
                if mount["target"] != "/backup-status":
                    service["volumes"].append({**mount, "read_only": True})
                    if mount["type"] == "volume":
                        model.setdefault("volumes", {})[mount["source"]] = source_model["volumes"][
                            mount["source"]
                        ]
            # Read-only source mounts require restic's explicit no-lock mode.
            service["environment"]["BACKUP_READ_ONLY"] = "1"
        directories = (
            "/backup-status" if source and source != environment else "/backups /backup-status"
        )
        self.compose_model(
            environment,
            model,
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "--user",
            "0:0",
            "--entrypoint",
            "sh",
            "backup",
            "-c",
            "install -d -o 10001 -g 10001 " + directories,
        )
        arguments = ["run", "--rm", "--no-deps", "--pull", "never"]
        if protect:
            arguments += ["--env", "BACKUP_PROTECT=1"]
        if snapshot:
            require(
                re.fullmatch("[0-9a-f]{64}", snapshot),
                "an exact backup snapshot ID is required",
            )
            arguments += ["--env", "BACKUP_RESTORE_SNAPSHOT=" + snapshot]
        if operation == "restore":
            arguments += [
                "--env",
                "BACKUP_RESTORE_CONFIRMATION=restore-production-database",
            ]
        if operation == "snapshots":
            arguments += ["--env", "BACKUP_SNAPSHOTS_JSON=1"]
        result = self.compose_model(
            environment, model, *arguments, "backup", operation, capture=True
        )
        if not capture:
            print(result)
        return result

    def snapshots(self, environment):
        # Discovery must work on a replacement host before any release is loaded.
        output = os.environ.get("ENV_FILE")
        if output:
            path = self.root / output
            require(
                path.resolve().is_relative_to(self.local.resolve()),
                "generated recovery inputs must remain under .local",
            )
            path.unlink(missing_ok=True)
        settings = self.settings(environment)
        tool = os.environ.get("BACKUP_DISCOVERY_IMAGE", f"local/{self.project}-backup:discovery")
        # Explicit preparation owns building discovery tooling; discovery itself
        # never initializes or writes the backup repository.
        password = settings["BACKUP_PASSWORD"]
        repository = settings.get("BACKUP_REPOSITORY", "/backups")
        local_path = Path(
            settings.get("BACKUP_LOCAL_PATH", str(self.state_dir(environment) / "backups"))
        )
        mounts = []
        if environment == "ephemeral":
            _, model = self.current_model(environment)
            backup = model["services"]["backup"]
            mount = next(item for item in backup["volumes"] if item["target"] == "/backups")
            volume = model["volumes"][mount["source"]]["name"]
            run("docker", "volume", "inspect", volume, capture=True)
            mounts += ["--volume", f"{volume}:/backups:ro"]
            repository = backup["environment"]["RESTIC_REPOSITORY"]
            password = backup["environment"]["RESTIC_PASSWORD"]
        else:
            require(
                local_path.exists(), "configure existing backup repository access before discovery"
            )
            mounts += ["--volume", f"{local_path}:/backups:ro"]
        rclone_config = settings.get("BACKUP_RCLONE_CONFIG", "/config/rclone/rclone.conf")
        if repository.startswith("rclone:"):
            config_dir = Path(
                settings.get(
                    "BACKUP_RCLONE_CONFIG_DIR", str(self.state_dir(environment) / "rclone")
                )
            )
            relative = Path(rclone_config).relative_to("/config/rclone")
            require(
                (config_dir / relative).is_file(),
                "configure the existing rclone credentials before discovery",
            )
            mounts += ["--volume", f"{config_dir}:/config/rclone:ro"]
        command = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--env",
            "RESTIC_PASSWORD",
            "--env",
            "RESTIC_REPOSITORY",
            "--env",
            "RCLONE_CONFIG",
            *mounts,
            "--entrypoint",
            "restic",
            tool,
            "snapshots",
            "--no-lock",
            "--json",
            "--host",
            settings.get("BACKUP_HOST", self.project + "-" + environment),
            "--tag",
            settings.get("BACKUP_TAG", "postgres"),
        ]
        raw = run(
            *command,
            env={
                **os.environ,
                "RESTIC_PASSWORD": password,
                "RESTIC_REPOSITORY": repository,
                "RCLONE_CONFIG": rclone_config,
            },
            capture=True,
        )
        snapshots = json.loads(raw)
        if os.environ.get("SELECT") != "newest":
            print(json.dumps(snapshots, indent=2))
            return
        require(output, "SELECT=newest requires ENV_FILE")
        require(snapshots, "no backups for the logical deployment")
        selected = max(
            snapshots,
            key=lambda value: datetime.datetime.fromisoformat(value["time"].replace("Z", "+00:00")),
        )
        tags = selected.get("tags", [])
        revisions = [
            value.split(":", 1)[1] for value in tags if value.startswith("release-revision:")
        ]
        identities = [
            value.split(":", 1)[1] for value in tags if value.startswith("release-identity:")
        ]
        require(
            len(revisions) == 1,
            "newest snapshot has missing or inconsistent release metadata",
        )
        rev = revision(revisions[0])
        require(
            len(identities) == 1 and re.fullmatch("[0-9a-f]{64}", identities[0]),
            "newest snapshot has missing or inconsistent image identity",
        )
        identifier = selected["id"]
        require(re.fullmatch("[0-9a-f]{64}", identifier), "invalid snapshot identity")
        atomic(
            path,
            f"export BACKUP_SNAPSHOT={shlex.quote(identifier)}\nexport RELEASE_REVISION={shlex.quote(rev)}\nexport RELEASE_IDENTITY={shlex.quote(identities[0])}\n",
        )
        print(f"Selected snapshot {identifier} at release {rev}; inputs: {path}")

    def database_state(self, environment, data=None):
        if data is None:
            code = "from django.db import connection; c=connection.cursor(); c.execute(\"SELECT to_regclass('public.scaffold_release_state')\"); present=c.fetchone()[0]; c.execute('SELECT state FROM scaffold_release_state WHERE id=1') if present else None; print(c.fetchone()[0] if present else '{}')"
            return json.loads(
                self.manage(environment, "shell", "-c", code, capture=True).splitlines()[-1]
            )
        payload = json.dumps(data)
        code = (
            "from django.db import connection; c=connection.cursor(); c.execute('CREATE TABLE IF NOT EXISTS scaffold_release_state (id integer PRIMARY KEY, state text NOT NULL)'); c.execute('INSERT INTO scaffold_release_state (id,state) VALUES (1,%s) ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state', ["
            + repr(payload)
            + "])"
        )
        self.manage(environment, "shell", "-c", code)

    def migrate(self, environment, pending=None):
        previous = self.database_state(environment)
        require(
            previous.get("status", "ready") == "ready",
            "database has an incomplete migration; recover its recorded snapshot before retrying",
        )
        protect = pending is not None and bool(previous.get("release"))
        if protect:
            point = self.state_dir(environment) / "rollback.json"
            active = read_json(point).get("snapshot") if point.exists() else None
            self.backup(environment, "reserve-point", active, capture=True)
        output = self.backup(environment, capture=True, protect=protect)
        snapshot = next(
            (
                line.removeprefix("SNAPSHOT_ID=")
                for line in output.splitlines()
                if line.startswith("SNAPSHOT_ID=")
            ),
            None,
        )
        require(
            snapshot and re.fullmatch("[0-9a-f]{64}", snapshot),
            "backup did not return its exact snapshot ID",
        )
        if pending is not None:
            pending["snapshot"] = snapshot
            pending["previous"] = previous.get("release")
            write_json(
                self.state_dir(environment) / "attempts" / (pending["id"] + ".json"),
                pending,
            )
            if pending["previous"]:
                write_json(self.state_dir(environment) / "rollback.json", pending)
                self.backup(environment, "commit-point", snapshot, capture=True)
        token = secrets.token_hex(16)
        self.database_state(
            environment,
            {"status": "migrating", "token": token, "revision": self.head()},
        )
        self.manage(environment, "migrate", "--noinput")
        release = self.release() if environment != "dev" else None
        self.database_state(
            environment,
            {
                "status": "ready",
                "token": token,
                "revision": self.head(),
                "release": release,
            },
        )
        return snapshot

    def retain(self, environment, release):
        destination = self.state_dir(environment) / "releases" / release_identity(release)
        source = self.release_dir(release["revision"])
        require(
            (source / "images.tar.gz").is_file() and (source / "source.tar").is_file(),
            "release archives required for reliable rollback",
        )
        if not destination.exists():
            temporary = destination.with_name(destination.name + ".pending")
            temporary.parent.mkdir(parents=True, exist_ok=True)
            if temporary.exists():
                shutil.rmtree(temporary)
            shutil.copytree(source, temporary)
            os.rename(temporary, destination)

    def preflight_release(self, environment, release):
        self.clean_source()
        for role, reference in release.get("image_ids", release["images"]).items():
            info = self.inspect_image(reference, release["platform"])
            if role in ("backend", "web", "backup"):
                require(
                    (info["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
                    == release["revision"],
                    "image revision label mismatch",
                )
        verify_seal(self.release_dir())
        for name in (
            "source-check",
            "image-check",
            *(("runtime-check",) if environment == "prod" else ()),
        ):
            check = read_json(self.release_dir() / (name + ".json"))
            require(
                check.get("revision") == release["revision"],
                "validation evidence revision mismatch",
            )
            if name == "source-check":
                require(
                    check.get("profile") == self.profile,
                    "validation evidence profile mismatch",
                )
            else:
                require(
                    check.get("images") == release["images"],
                    "validation evidence image set mismatch",
                )

    def startup_inputs(self, environment, service=None):
        release = None if environment == "dev" else self.release()
        _, model = self.config(environment, release)
        require(not service or service in model["services"], "unknown service selector")
        self.preflight_ports(environment, model, service)
        if service:
            self.require_dependencies(environment, service, model)
        if release:
            self.preflight_release(environment, release)
            self.retain(environment, release)
        return release, model

    def up(self, environment, service=None):
        release, model = self.startup_inputs(environment, service)
        if service and service not in (self.app,):
            identifier = secrets.token_hex(16)
            attempt = {
                "id": identifier,
                "revision": self.head(),
                "service": service,
                "images": {service: model["services"][service]["image"]},
                "outcome": "started",
            }
            record = self.state_dir(environment) / "attempts" / (identifier + ".json")
            write_json(record, attempt)
            try:
                self.compose(
                    environment,
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                    "--no-deps",
                    "--wait",
                    service,
                    release=release,
                )
                _, current = self.current_model(environment)
                current["services"][service] = model["services"][service]
                current.setdefault("volumes", {}).update(model.get("volumes", {}))
                write_json(self.state_dir(environment) / "runtime.json", current)
                attempt["outcome"] = "succeeded"
            except (Rejected, OSError):
                attempt["outcome"] = "failed"
                raise
            finally:
                write_json(record, attempt)
            return
        attempt = {
            "id": datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
            + "-"
            + secrets.token_hex(4),
            "intended": release,
            "outcome": "started",
            "revision": self.head(),
        }
        previous = self.state_dir(environment) / "current.json"
        change = (
            not previous.exists()
            or read_json(previous).get("release") != release
            or read_json(previous).get("revision") != self.head()
        )
        try:
            if not service:
                self.compose(
                    environment,
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "db",
                    release=release,
                )
            self.migrate(environment, attempt if change else None)
            if environment != "dev":
                self.manage(environment, "collectstatic", "--noinput")
            services = (
                [service]
                if service
                else [
                    name
                    for name in model["services"]
                    if name != "backup" or self.backup_profile == "immutable-backup"
                ]
            )
            self.compose(
                environment,
                "up",
                "--detach",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                *(["--no-deps"] if service else []),
                *services,
                release=release,
            )
            attempt["outcome"] = "succeeded"
            write_json(previous, {"revision": self.head(), "release": release})
            if service:
                _, current = self.current_model(environment)
                current["services"][service] = model["services"][service]
                current.setdefault("volumes", {}).update(model.get("volumes", {}))
                model = current
            write_json(self.state_dir(environment) / "runtime.json", model)
        except (Rejected, OSError):
            attempt["outcome"] = "failed"
            raise
        finally:
            if change:
                write_json(
                    self.state_dir(environment) / "attempts" / (attempt["id"] + ".json"),
                    attempt,
                )

    def down(self, environment, service=None):
        _, model = self.current_model(environment)
        require(not service or service in model["services"], "unknown service selector")
        self.current_compose(environment, "stop", *([service] if service else []))

    def restore(self, environment, snapshot=None, authorized=False):
        require(
            authorized or os.environ.get("CONFIRM") == "restore-" + environment,
            "database replacement requires CONFIRM=restore-" + environment,
        )
        snapshot = snapshot or os.environ.get("BACKUP_SNAPSHOT")
        require(
            snapshot and re.fullmatch("[0-9a-f]{64}", snapshot),
            "set BACKUP_SNAPSHOT to an exact ID",
        )
        # Validate identity and backup existence before stopping any writers.
        self.release() if environment != "dev" else None
        source = os.environ.get("BACKUP_SOURCE_ENV") or environment
        self.backup(environment, "snapshot-exists", snapshot, capture=True, source=source)
        _, model = self.config(environment)
        for writer in (self.app, "monitor", "backup"):
            if writer in model["services"]:
                self.down(environment, writer)
        self.up(environment, "db")
        self.backup(environment, "restore", snapshot, source=source)

    def verify(self, environment):
        settings = self.settings(environment)
        host = settings.get("HOST_INGRESS_HOST", "localhost")
        base = "http://127.0.0.1:" + settings.get(
            "APP_PORT", "8080" if environment == "dev" else "18080"
        )
        headers = {"Host": host}
        if environment != "dev":
            headers["X-Forwarded-Proto"] = "https"
        for path in ("/api/healthz", "/", "/static/smoketest.txt"):
            if (
                environment == "dev"
                and path.startswith("/static/")
                and self.profile == "server-rendered-django"
            ):
                continue
            request = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                content = response.read()
                require(response.status == 200, "runtime smoke failed")
                if path == "/api/healthz":
                    data = json.loads(content)
                    require(data.get("status") == "ok", "application is unhealthy")
                    require(
                        data.get("revision") == self.head(),
                        "running application revision differs from checkout",
                    )
                elif path == "/static/smoketest.txt":
                    require(
                        content.strip()
                        == (self.root / "backend/src/app/project/static/smoketest.txt")
                        .read_bytes()
                        .strip(),
                        "static smoke returned another page",
                    )
                else:
                    require(
                        b'id="root"' in content or b"<main" in content,
                        "application root did not render",
                    )
        print(f"Verified {environment} revision {self.head()}")

    def integration(self, environment):
        self.verify(environment)
        self.manage(environment, "test_deployment")
        # Exercise the configured backup tooling against real application data.
        self.backup(environment, "once")
        self.backup(environment, "verify")
        release = self.release() if environment != "dev" else None
        if release:
            write_json(
                self.release_dir() / "runtime-check.json",
                {
                    "revision": self.head(),
                    "images": release["images"],
                    "platform": release["platform"],
                    "source": "local-runtime-checks",
                    "profile": self.profile,
                    "backup_profile": self.backup_profile,
                    "monitoring": os.environ.get("MONITORING", "0") == "1",
                    "passed_at": time.time(),
                },
            )
            seal_release(self.release_dir())

    def rollback(self, environment):
        require(
            os.environ.get("CONFIRM") == "rollback-" + environment,
            "rollback requires CONFIRM=rollback-" + environment,
        )
        record = read_json(self.state_dir(environment) / "rollback.json")
        rev, snapshot = (
            os.environ.get("RELEASE_REVISION"),
            os.environ.get("BACKUP_SNAPSHOT"),
        )
        require(
            bool(rev) == bool(snapshot),
            "supply RELEASE_REVISION and BACKUP_SNAPSHOT together",
        )
        rev = revision(rev or (record.get("previous") or {}).get("revision"))
        snapshot = snapshot or record.get("snapshot")
        self.checkout(rev)
        previous = record.get("previous")
        retained = (
            self.state_dir(environment) / "releases" / release_identity(previous)
            if previous and previous["revision"] == rev
            else None
        )
        self.download(rev, retained=retained)
        self.setup()
        self.image_check()
        self.restore(environment, snapshot, authorized=True)
        self.up(environment)
        self.verify(environment)

    def checkout(self, rev):
        revision(rev)
        require(
            not self.git("status", "--porcelain"),
            "commit or stash source changes before selecting another revision",
        )
        self.git("switch", "--detach", rev)

    def ci(self):
        self.setup()
        self.check()
        self.build()
        self.image_check()
        self.prepare("ephemeral")
        with locked(self.state_dir("ephemeral")):
            try:
                self.up("ephemeral")
                self.integration("ephemeral")
                self.verify("ephemeral")
            finally:
                self.remove("ephemeral")

    def remove(self, environment):
        # Namespace checks happen before Compose sees a destructive request.
        path, model = self.current_model(environment)
        require(
            model["name"] == self.project + "-" + environment,
            "cleanup namespace mismatch",
        )
        self.current_compose(environment, "down", "--volumes", "--remove-orphans")
        if environment == "ephemeral":
            shutil.rmtree(self.state_dir(environment), ignore_errors=False)

    def dependencies(self):
        path = self.root / "deploy/dependencies.json"
        data = read_json(path)
        for dependency in data["images"].values():
            source = dependency["source"]
            require(
                re.fullmatch("[a-z0-9][a-z0-9._:/-]*", source),
                "invalid declared image source",
            )
            info = json.loads(
                run(
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
        project = "backend" if self.profile == "react-vite" else "profiles/server-rendered-django"
        run(
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{self.root}:/workspace",
            "--workdir",
            "/workspace",
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
        if self.profile == "react-vite":
            run(
                "docker",
                "run",
                "--rm",
                "--env",
                "CI=true",
                "--volume",
                f"{self.root}:/workspace",
                "--workdir",
                "/workspace/frontend",
                data["images"]["node"]["pin"],
                "sh",
                "-c",
                "corepack enable && pnpm install --lockfile-only --ignore-scripts",
            )

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

    def repository(self):
        remote = self.git("remote", "get-url", "origin")
        match = re.search(r"github.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
        require(match, "configure a GitHub origin for release publication/download")
        return match.group(1)

    def bundle_metadata(self):
        release = self.release()
        directory = self.release_dir()
        verify_seal(directory)
        evidence = {
            "source": read_json(directory / "source-check.json"),
            "images": read_json(directory / "image-check.json"),
            "runtime": read_json(directory / "runtime-check.json"),
        }
        for role, variable in (
            ("backend", "RELEASE_BACKEND_SBOM"),
            ("web", "RELEASE_WEB_SBOM"),
        ):
            if os.environ.get(variable):
                evidence.setdefault("sboms", {})[role] = read_json(self.root / os.environ[variable])
        for check in (evidence[key] for key in ("source", "images", "runtime")):
            require(check["revision"] == release["revision"], "validation revision mismatch")
        for key in ("images", "runtime"):
            require(
                evidence[key]["images"] == release["images"],
                "validation image set mismatch",
            )
        write_json(directory / "evidence.json", evidence)
        names = ("manifest.json", "source.tar", "source.bundle", "evidence.json")
        hashes = {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names
        }
        write_json(directory / "checksums.json", hashes)
        artifact = directory / "release.tar"
        with tarfile.open(artifact, "w") as stream:
            for name in (*names, "checksums.json"):
                stream.add(directory / name, arcname=name, recursive=False)
        return artifact

    def publish(self):
        release = self.release()
        require(
            self.prefix.startswith("ghcr.io/"),
            "set RELEASE_IMAGE_PREFIX to the tenant GHCR repository",
        )
        # Check validation before any external publication.
        self.bundle_metadata()
        ids = dict(release.get("image_ids", release["images"]))
        for role, identity in ids.items():
            name = self.prefix + "-" + role
            tag = name + ":" + self.head() + "-" + release["platform"].split("/")[1]
            run("docker", "tag", identity, tag)
            run("docker", "push", tag)
            digests = self.inspect_image(tag)["RepoDigests"]
            matches = [value for value in digests if value.startswith(name + "@")]
            require(len(matches) == 1, "published image has no unambiguous immutable digest")
            release["images"][role] = immutable(matches[0])
        release["image_ids"] = ids
        release["prefix"] = self.prefix
        if os.environ.get("GITHUB_REF") == "refs/heads/main" and os.environ.get("GITHUB_RUN_ID"):
            release["publication"] = {
                "repository": self.repository(),
                "run_id": str(os.environ["GITHUB_RUN_ID"]),
                "ref": "refs/heads/main",
            }
        directory = self.release_dir()
        write_json(directory / "manifest.json", release)
        # Evidence records the built content IDs, even when publication adds OCI references.
        for file in ("image-check.json", "runtime-check.json"):
            check = read_json(directory / file)
            check["images"] = release["images"]
            write_json(directory / file, check)
        seal_release(directory)
        artifact = self.bundle_metadata()
        name = "release-" + self.profile + "-" + release["platform"].replace("/", "-") + ".tar"
        publish_path = directory / name
        shutil.copyfile(artifact, publish_path)
        tag = "release-" + self.head()
        result = subprocess.run(
            ["gh", "release", "view", tag, "--repo", self.repository()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            run(
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                self.repository(),
                "--target",
                self.head(),
                "--title",
                "Release " + self.head(),
                "--notes",
                "Recorded immutable release; validation evidence and committed source are included.",
                str(publish_path),
            )
        else:
            run(
                "gh",
                "release",
                "upload",
                tag,
                str(publish_path),
                "--repo",
                self.repository(),
            )

    def download(self, rev=None, retained=None, expected_run=None):  # noqa: C901 -- ordered artifact preflight before image loading
        from release_bundle import Rejected as ArchiveRejected
        from release_bundle import validate_images

        rev = revision(rev or os.environ.get("RELEASE_REVISION") or self.head())
        directory = self.release_dir(rev)
        destination = directory
        if expected_run and directory.exists():
            cached = self.release(rev)
            if cached.get("publication", {}).get("run_id") != str(expected_run):
                (self.local / "downloads").mkdir(parents=True, exist_ok=True)
                directory = (
                    Path(tempfile.mkdtemp(prefix="published-", dir=self.local / "downloads"))
                    / "release"
                )
        if retained:
            require(Path(retained).is_dir(), "exact retained rollback artifact is missing")
            verify_seal(retained)
            if directory.exists():
                shutil.rmtree(directory)
            shutil.copytree(retained, directory)
        remote = not directory.exists()
        if not remote:
            verify_seal(directory)
        if not (directory / "manifest.json").is_file():
            name = "release-" + self.profile + "-" + self.architecture().replace("/", "-") + ".tar"
            temporary = self.local / "downloads" / rev
            temporary.mkdir(parents=True, exist_ok=True)
            run(
                "gh",
                "release",
                "download",
                "release-" + rev,
                "--repo",
                self.repository(),
                "--pattern",
                name,
                "--dir",
                temporary,
            )
            directory.mkdir(parents=True, exist_ok=True)
            with tarfile.open(temporary / name) as archive:
                expected = {
                    "manifest.json",
                    "source.tar",
                    "source.bundle",
                    "evidence.json",
                    "checksums.json",
                }
                members = archive.getmembers()
                require(
                    {item.name for item in members} == expected and len(members) == len(expected),
                    "incomplete or unexpected release bundle contents",
                )
                for item in archive.getmembers():
                    require(item.isfile(), "release bundle links are forbidden")
                    (directory / item.name).write_bytes(archive.extractfile(item).read())
        release = validate_release(
            read_json(directory / "manifest.json"), self.profile, self.architecture()
        )
        require(release["revision"] == rev, "downloaded release revision mismatch")
        if os.environ.get("RELEASE_IDENTITY"):
            require(
                release_identity(release) == os.environ["RELEASE_IDENTITY"],
                "release image set differs from the selected backup",
            )
        run("git", "bundle", "verify", directory / "source.bundle", cwd=self.root, capture=True)
        heads = run(
            "git", "bundle", "list-heads", directory / "source.bundle", cwd=self.root, capture=True
        )
        require(heads.split() == [rev, "HEAD"], "source bundle revision mismatch")
        if expected_run:
            require(
                release.get("publication")
                == {
                    "repository": self.repository(),
                    "run_id": str(expected_run),
                    "ref": "refs/heads/main",
                },
                "selected successful main run has no matching published release",
            )
        source = subprocess.check_output(["git", "archive", "--format=tar", rev], cwd=self.root)
        require(
            hashlib.sha256(source).digest()
            == hashlib.sha256((directory / "source.tar").read_bytes()).digest(),
            "release deployment source differs from the exact commit",
        )
        if (directory / "checksums.json").exists():
            checksums = read_json(directory / "checksums.json")
            require(
                set(checksums) == {"manifest.json", "source.tar", "source.bundle", "evidence.json"},
                "incomplete metadata checksums",
            )
            for name, digest in checksums.items():
                require(
                    name in ("manifest.json", "source.tar", "source.bundle", "evidence.json"),
                    "invalid checksum path",
                )
                require(
                    hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest,
                    "release bundle checksum mismatch",
                )
        if (directory / "evidence.json").exists():
            checks = read_json(directory / "evidence.json")
            for name, key in (
                ("source-check", "source"),
                ("image-check", "images"),
                ("runtime-check", "runtime"),
            ):
                require(
                    checks.get(key, {}).get("revision") == rev,
                    "incomplete or inconsistent release validation evidence",
                )
                write_json(directory / (name + ".json"), checks[key])
        identities = release.get("image_ids", release["images"])
        if not (directory / "images.tar.gz").exists():
            require(remote, "local release image archive is incomplete")
            for reference in release["images"].values():
                require(
                    "@sha256:" in reference,
                    "missing local image archive cannot be rebuilt or recovered from a mutable tag",
                )
                run("docker", "pull", "--platform", release["platform"], reference)
            save_images(directory / "images.tar.gz", identities.values())
        if (directory / "integrity.json").exists():
            verify_seal(directory)
        try:
            validate_images(
                directory / "images.tar.gz",
                {key: {"id": value} for key, value in identities.items()},
                release["platform"],
            )
        except ArchiveRejected as error:
            raise Rejected(str(error)) from error
        run("docker", "image", "load", "--input", directory / "images.tar.gz")
        seal_release(directory)
        if directory != destination:
            retained_build = self.local / "retained-builds" / release_identity(self.release(rev))
            retained_build.parent.mkdir(parents=True, exist_ok=True)
            require(
                not retained_build.exists(),
                "a different cached build is already retained; inspect it before replacement",
            )
            destination.rename(retained_build)
            directory.rename(destination)
        print(f"Prepared release {rev} without changing the checkout or deployment")

    def latest(self):
        data = json.loads(
            run(
                "gh",
                "api",
                f"repos/{self.repository()}/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=1",
                capture=True,
            )
        )
        require(
            data.get("workflow_runs"),
            "no successful main release; no older release selected",
        )
        rev = revision(data["workflow_runs"][0]["head_sha"])
        # Fetch exactly the selected commit rather than moving a branch ref.
        run("git", "fetch", "origin", rev, cwd=self.root)
        self.checkout(rev)
        self.download(rev, expected_run=data["workflow_runs"][0]["id"])

    def dispatch(self, goal):  # noqa: C901 -- explicit public command routing
        operation, environment, service = target(goal)
        if operation == "help":
            print(
                (self.root / "docs/makefile-design.md")
                .read_text()
                .split("## Command results", 1)[1]
                .split("## Validation boundaries", 1)[0]
            )
            return
        if operation == "profiles":
            print(json.dumps(self.profiles))
            return
        if operation == "setup":
            self.setup()
        elif operation == "hooks":
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
        elif operation == "check":
            self.check()
        elif operation in (
            "format-check",
            "lint",
            "typecheck",
            "migrations-check",
            "framework-check",
            "test",
            "migrations",
            "format",
        ):
            self.gate(operation)
        elif operation == "build":
            self.build()
        elif operation == "image-check":
            self.image_check()
        elif operation == "deps-lock":
            self.dependencies()
        elif operation == "download":
            self.download()
        elif operation == "download-latest":
            self.latest()
        elif operation == "publish":
            self.publish()
        elif operation == "clean":
            for name in (".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"):
                for path in (self.root / "backend").rglob(name):
                    if ".venv" not in path.parts:
                        shutil.rmtree(path)
        elif operation == "prepare":
            self.prepare(environment)
        elif operation == "config":
            path, _ = self.config(environment)
            print(path)
        elif operation == "history":
            directory = self.state_dir(environment) / "attempts"
            print(
                json.dumps(
                    [read_json(path) for path in sorted(directory.glob("*.json"))],
                    indent=2,
                )
            )
        elif operation == "ci":
            self.ci()
        elif operation == "snapshots":
            with locked(self.state_dir(environment)):
                self.snapshots(environment)
        elif operation in ("ps", "logs", "shell"):
            _, model = self.current_model(environment)
            require(not service or service in model["services"], "unknown service selector")
            if operation == "shell":
                self.current_compose(environment, "exec", service or self.app, "sh")
            else:
                self.current_compose(
                    environment,
                    operation,
                    *(["--tail", "200"] if operation == "logs" else []),
                    *([service] if service else []),
                )
        else:
            with locked(self.state_dir(environment)):
                if operation == "up":
                    self.up(environment, service)
                elif operation == "down":
                    self.down(environment, service)
                elif operation == "restart":
                    self.startup_inputs(environment, service)
                    self.down(environment, service)
                    self.up(environment, service)
                elif operation == "rm":
                    self.remove(environment)
                elif operation == "migrate":
                    self.migrate(environment)
                elif operation == "restore":
                    self.restore(environment)
                elif operation == "rollback":
                    self.rollback(environment)
                elif operation == "backup":
                    self.backup(environment)
                elif operation == "backup-test":
                    self.backup(environment, "verify")
                elif operation == "verify":
                    self.verify(environment)
                elif operation == "integration-test":
                    self.integration(environment)
                elif operation == "monitor-check":
                    self.current_compose(
                        environment,
                        "run",
                        "--rm",
                        "--no-deps",
                        "--pull",
                        "never",
                        "--entrypoint",
                        "python",
                        self.app,
                        "-m",
                        "app.manage",
                        "monitor_operational_integrity",
                    )
                elif operation in ("superuser", "console"):
                    self.current_compose(
                        environment,
                        "exec",
                        self.app,
                        "python",
                        "-m",
                        "app.manage",
                        "createsuperuser" if operation == "superuser" else "shell",
                    )


def main():
    try:
        require(len(sys.argv) == 2, "invoke through a documented Make target")
        Operator().dispatch(sys.argv[1])
    except (Rejected, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
