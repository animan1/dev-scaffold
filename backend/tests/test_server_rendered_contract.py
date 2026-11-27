from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "SCAFFOLD_PROFILE=server-rendered-django", *arguments],
        cwd=_repository_root(),
        check=check,
        capture_output=True,
        text=True,
    )


def test_server_rendered_make_contract_and_quality_gates() -> None:
    makefile = (_repository_root() / "profiles/server-rendered-django/profile.mk").read_text()
    makefile += (_repository_root() / "Makefile").read_text()

    for target in (
        "build",
        "up-dev",
        "down-dev",
        "logs-dev",
        "ps-dev",
        "reset-dev",
        "format",
        "lint",
        "typecheck",
        "test",
        "coverage",
        "check",
        "verify",
        "precommit",
        "build-production",
        "up-prod",
        "smoke-prod",
        "down-prod",
        "smoke",
    ):
        assert f"\n{target}:" in makefile
    for gate in (
        "format-check",
        "lint",
        "typecheck",
        "coverage",
        "migrations-check",
        "django-check",
    ):
        assert f"$(MAKE) {gate}" in makefile


def test_server_rendered_teardown_is_safe_and_reset_is_guarded() -> None:
    down = _make("--dry-run", "down-dev").stdout
    assert "down --remove-orphans" in down
    assert "down -v" not in down

    reset = _make("reset-dev", check=False)
    assert reset.returncode != 0
    assert "Refusing to remove development volumes." in reset.stdout

    confirmed = _make("--dry-run", "reset-dev", "CONFIRM_RESET=1").stdout
    assert "down -v --remove-orphans" in confirmed
    assert " up " not in confirmed


def test_server_rendered_diagnostics_use_selected_compose_and_help() -> None:
    help_output = _make("help").stdout
    logs = _make("--dry-run", "logs-dev").stdout
    ps = _make("--dry-run", "ps-dev").stdout

    assert "logs-dev" in help_output
    assert "ps-dev" in help_output
    assert "profiles/server-rendered-django/compose.yml logs -f --tail=200" in logs
    assert "COMPOSE_PROJECT_NAME=" in logs
    assert "profiles/server-rendered-django/compose.yml ps" in ps
    assert "COMPOSE_PROJECT_NAME=" in ps


def test_server_rendered_precommit_is_a_recipe() -> None:
    output = _make("--dry-run", "precommit").stdout
    assert "docker compose" in output
    assert "uv run --project /workspace/profiles/server-rendered-django --no-sync" in output
    assert '--config ".pre-commit-config.yaml" --all-files' in output
    assert "corepack pnpm" not in output


def test_prod_and_ephemeral_use_the_same_recorded_release_workflow() -> None:
    project_name = _repository_root().name
    production = _make("--dry-run", "up-prod").stdout
    ephemeral = _make("--dry-run", "up-ephemeral", "HOST_INGRESS=0").stdout
    for suffix, output in (("prod", production), ("ephemeral", ephemeral)):
        assert f"COMPOSE_PROJECT_NAME={project_name}-{suffix}" in output
        assert "profiles/server-rendered-django/backend.Dockerfile" not in output
        assert "profiles/server-rendered-django/release-nginx.Dockerfile" not in output
        assert "Set RELEASE_FILE" in output
        database_start = output.index("up -d --no-build db")
        production_check = output.index("python -m app.manage check --deploy")
        migration = output.index("python -m app.manage migrate")
        static_collection = output.index("python -m app.manage collectstatic --noinput --clear")
        application_start = output.index("up -d --no-build\n")
        assert database_start < production_check < migration < static_collection < application_start
    assert "standalone.compose.yml" not in production
    assert "release-ci-only" not in production
    assert "pull app web" in production
    assert "standalone.compose.yml" in ephemeral
    assert "release-ci-only" in ephemeral
    assert "pull app web" not in ephemeral
    assert "deploy/.env.prod" not in ephemeral

    down = _make("--dry-run", "down-prod").stdout
    assert f"COMPOSE_PROJECT_NAME={project_name}-prod" in down
    assert "down --remove-orphans" in down
    assert "down -v" not in down


def test_server_rendered_ephemeral_uses_local_tls_ingress() -> None:
    root = _repository_root()
    ingress = (root / "profiles/server-rendered-django/ingress.conf.template").read_text()
    web = (root / "profiles/server-rendered-django/release-nginx.conf").read_text()
    smoke = _make("--dry-run", "verify-ephemeral").stdout

    assert "return 308 https://$host:${INGRESS_HTTPS_PORT}$request_uri" in ingress
    assert "proxy_pass http://web:8080" in ingress
    assert "proxy_set_header Host $http_host" in ingress
    assert "proxy_set_header Host $http_host" in web
    assert "proxy_set_header X-Forwarded-Proto https" in ingress
    assert "http://127.0.0.1:18081/" in smoke
    assert "https://127.0.0.1:18444/" in smoke
    assert "/admin/login/\\?next=/admin/" in smoke
    assert "set-cookie: csrftoken=.*; secure" in smoke
    custom = _make(
        "--dry-run",
        "up-ephemeral",
        "verify-ephemeral",
        "HOST_INGRESS_HOST=tenant.example",
        "SCAFFOLD_BACKUP_PROFILE=none",
        "HOST_INGRESS=0",
    ).stdout
    assert "-subj '/CN=tenant.example'" in custom
    assert "docker build" not in custom
    assert '--cacert "' in custom
    assert "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,tenant.example" in custom
    assert "DJANGO_CSRF_TRUSTED_ORIGINS=https://tenant.example" in custom
    assert "Host: tenant.example" in custom
    assert "location: https://tenant.example:" in custom


def test_server_rendered_production_security_fails_closed() -> None:
    settings = (_repository_root() / "backend/src/app/project/settings/prod.py").read_text()

    assert "SECURE_SSL_REDIRECT = True" in settings
    assert "SESSION_COOKIE_SECURE = True" in settings
    assert "CSRF_COOKIE_SECURE = True" in settings
    assert 'env.bool("DJANGO_SECURE_SSL_REDIRECT"' not in settings
    assert 'env.bool("DJANGO_SESSION_COOKIE_SECURE"' not in settings
    assert 'env.bool("DJANGO_CSRF_COOKIE_SECURE"' not in settings


def test_server_rendered_profile_exposes_django_admin() -> None:
    urls = (_repository_root() / "backend/src/app/project/urls.py").read_text()

    assert "from django.contrib import admin" in urls
    assert 'path("admin/", admin.site.urls)' in urls


def test_server_rendered_ephemeral_wrapper_selects_the_profile() -> None:
    script = _repository_root() / "profiles/server-rendered-django/ephemeral"
    project_name = _repository_root().name

    assert os.access(script, os.X_OK)
    output = subprocess.run(
        [script, "up", "--dry-run"],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "profiles/server-rendered-django/standalone.compose.yml" in output
    assert f"COMPOSE_PROJECT_NAME={project_name}-ephemeral" in output
    assert "deploy/docker-compose.prod.yml" not in output

    smoke = subprocess.run(
        [script, "smoke", "--dry-run"],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "http://127.0.0.1:18081/" in smoke
    assert "https://127.0.0.1:18444/" in smoke


def test_server_rendered_dependency_lock_uses_pinned_dockerized_uv() -> None:
    output = _make("--dry-run", "deps.lock").stdout

    assert "docker run --rm" in output
    assert "ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim" in output
    assert "lock --project profiles/server-rendered-django" in output
    assert "deps.lock" in _make("help").stdout


def test_server_rendered_django_migration_commands_use_docker() -> None:
    migrations = _make("--dry-run", "migrations").stdout
    migrate = _make("--dry-run", "migrate").stdout

    assert "docker compose" in migrations
    assert "python -m app.manage makemigrations" in migrations
    assert "docker compose" in migrate
    assert "python -m app.manage migrate" in migrate


@pytest.mark.parametrize(
    "profile,service",
    [
        ("react-vite", "backend"),
        ("server-rendered-django", "app"),
    ],
)
def test_development_startup_uses_selected_port_without_migrations(
    profile: str, service: str
) -> None:
    if not shutil.which("docker"):
        pytest.skip("Needs the Docker Compose client")
    root = _repository_root()
    wrapper = subprocess.check_output(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            "ps-dev",
            f"SCAFFOLD_PROFILE={profile}",
            "PROJECT_NAME=tenant",
            "DEV_HTTP_PORT=18123",
            "APP_HOST=dev.example",
        ],
        cwd=root,
        text=True,
    )
    arguments = shlex.split(wrapper)
    docker = arguments.index("docker")
    result = subprocess.run(
        [*arguments[docker:-1], "config", "--format", "json"],
        cwd=root,
        env={
            **os.environ,
            **dict(argument.split("=", 1) for argument in arguments[:docker]),
            "DOCKER_HOST": "unix:///nonexistent",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["name"] == "tenant-dev"
    services = config["services"]
    context = root / "backend" if service == "backend" else root
    assert services[service]["build"]["context"] == str(context)
    mount = "/app" if service == "backend" else "/workspace/backend"
    assert any(
        volume.get("target") == mount and volume.get("source") == str(root / "backend")
        for volume in services[service]["volumes"]
    )
    command = services[service]["command"]
    entry = "nginx" if service == "backend" else "app"
    assert services[entry]["ports"][0]["published"] == "18123"
    if service == "app":
        assert "dev.example" in services[service]["environment"]["DJANGO_ALLOWED_HOSTS"]
        assert "dev.example" in " ".join(services[service]["healthcheck"]["test"])
    else:
        assert services["frontend"]["environment"]["DEV_HTTP_PORT"] == "18123"
        assert services["frontend"]["environment"]["APP_HOST"] == "dev.example"
    assert "runserver" in " ".join(command)
    assert "migrate" not in " ".join(command)


def test_server_rendered_django_development_helpers_use_docker() -> None:
    help_output = _make("help").stdout
    superuser = _make("--dry-run", "superuser").stdout
    shell = _make("--dry-run", "shell").stdout

    assert "superuser" in help_output
    assert "shell" in help_output
    assert "fe.run" not in help_output
    assert "be.run" not in help_output
    assert "docker compose" in superuser
    assert "run --rm --workdir /workspace/profiles/server-rendered-django app" in superuser
    assert superuser.index("python -m app.manage migrate") < superuser.index(
        "python -m app.manage createsuperuser"
    )
    assert "python -m app.manage createsuperuser" in superuser
    assert "cd backend/src" not in superuser
    assert "docker compose" in shell
    assert "run --rm --workdir /workspace/profiles/server-rendered-django app" in shell
    assert "python -m app.manage shell" in shell
    assert "cd backend/src" not in shell


def test_server_rendered_help_includes_selected_optional_profiles() -> None:
    help_output = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "help").stdout

    assert "build-backup-image" in help_output
    assert "verify-backup-image" in help_output
    assert "backup-image-versions" in help_output
    assert "fe.run" not in help_output
    assert "be.run" not in help_output


def test_server_rendered_tools_explicitly_load_profile_configuration() -> None:
    assert (
        "ruff check --config /workspace/backend/pyproject.toml"
        in _make("--dry-run", "be.lint-in-container").stdout
    )
    assert (
        "mypy --config-file /workspace/backend/pyproject.toml"
        in _make("--dry-run", "be.typecheck-in-container").stdout
    )
    test_output = _make("--dry-run", "test-in-container").stdout
    assert "pytest -q -c" in test_output
    assert "profiles/server-rendered-django/pyproject.toml" in test_output
    assert "docker compose" not in test_output
    assert "pnpm" not in test_output
    host_test = _make("--dry-run", "test").stdout
    assert "be.test-in-container" in host_test
    assert "Makefile fe.test" in host_test
    assert "frontend sh" not in host_test
    coverage_output = _make("--dry-run", "coverage-in-container").stdout
    assert "pytest -c" in coverage_output
    assert "profiles/server-rendered-django/pyproject.toml" in coverage_output
    assert "--cov-config=pyproject.toml" in coverage_output


def test_changed_coverage_runs_from_backend() -> None:
    output = _make("--dry-run", "coverage-in-container").stdout

    assert f'cd "{_repository_root()}/backend"' in output
    assert "diff-cover" in output
    assert "coverage.xml" in output
    assert "backend/coverage.xml" not in output


@pytest.mark.skipif(shutil.which("diff-cover") is None, reason="Needs prepared source tooling")
@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_diff_cover_matches_changed_application_lines(tmp_path: Path, component: str) -> None:
    repository = tmp_path / "repository"
    backend = repository / component
    filename = "example.py" if component == "backend" else "example.ts"
    application = backend / "src" / "app" / filename
    application.parent.mkdir(parents=True)
    source = (
        "def answer() -> int:\n    return 1\n"
        if component == "backend"
        else "export function answer() {\n    return 1;\n}\n"
    )
    application.write_text(source)

    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    subprocess.run(["git", "add", f"{component}/src/app/{filename}"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "baseline"], cwd=repository, check=True)

    application.write_text(source.replace("return 1", "return 2"))
    (backend / "coverage.xml").write_text(
        """<?xml version="1.0" ?>
<coverage version="7.0" lines-valid="2" lines-covered="1" line-rate="0.5">
  <sources><source>.</source></sources>
  <packages><package name="app"><classes>
    <class name="example" filename="src/app/example.py">
      <lines><line number="1" hits="1"/><line number="2" hits="0"/></lines>
    </class>
  </classes></package></packages>
</coverage>
"""
    )

    report = "coverage.xml"
    if component == "frontend":
        report = "lcov.info"
        (backend / report).write_text(
            f"TN:\nSF:frontend/src/app/{filename}\nDA:1,1\nDA:2,0\nend_of_record\n"
        )
    result = subprocess.run(
        [
            "diff-cover",
            report,
            "--config-file",
            str(_repository_root() / "backend/pyproject.toml"),
            "--compare-branch=HEAD",
        ],
        cwd=backend,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert f"src/app/{filename}" in result.stdout
    assert "No lines with coverage information" not in result.stdout
