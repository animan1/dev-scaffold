from __future__ import annotations

import os
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

    for target in (
        "build",
        "up",
        "down",
        "reset",
        "deps.lock",
        "format",
        "lint",
        "typecheck",
        "test",
        "coverage",
        "check",
        "verify",
        "precommit",
        "build-production",
        "smoke",
    ):
        assert f"\n{target}:" in makefile
    for gate in (
        "format-check",
        "lint",
        "typecheck",
        "coverage",
        "changed-coverage",
        "migrations-check",
        "django-check",
    ):
        assert f"$(MAKE) {gate}" in makefile


def test_server_rendered_teardown_is_safe_and_reset_is_guarded() -> None:
    down = _make("--dry-run", "down").stdout
    assert "down --remove-orphans" in down
    assert "down -v" not in down

    reset = _make("reset", check=False)
    assert reset.returncode != 0
    assert "Refusing to remove development volumes." in reset.stdout

    confirmed = _make("--dry-run", "reset", "CONFIRM_RESET=1").stdout
    assert "down -v --remove-orphans" in confirmed
    assert " up " not in confirmed


def test_server_rendered_precommit_is_a_recipe() -> None:
    output = _make("--dry-run", "precommit").stdout
    assert "pre-commit run" in output
    assert "--all-files" in output


def test_server_rendered_dependency_lock_uses_pinned_dockerized_uv() -> None:
    output = _make("--dry-run", "deps.lock").stdout

    assert "docker run --rm" in output
    assert "ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim" in output
    assert "lock --project profiles/server-rendered-django" in output


def test_server_rendered_django_migration_commands_use_docker() -> None:
    migrations = _make("--dry-run", "migrations").stdout
    migrate = _make("--dry-run", "migrate").stdout

    assert "docker compose" in migrations
    assert "python -m app.manage makemigrations" in migrations
    assert "docker compose" in migrate
    assert "python -m app.manage migrate" in migrate


def test_server_rendered_django_development_helpers_use_docker() -> None:
    help_output = _make("help").stdout
    superuser = _make("--dry-run", "superuser").stdout
    shell = _make("--dry-run", "shell").stdout

    assert "superuser" in help_output
    assert "shell" in help_output
    assert "fe.run" not in help_output
    assert "be.run" not in help_output
    assert "docker compose" in superuser
    assert "run --rm app" in superuser
    assert superuser.index("python -m app.manage migrate") < superuser.index(
        "python -m app.manage createsuperuser"
    )
    assert "python -m app.manage createsuperuser" in superuser
    assert "cd backend/src" not in superuser
    assert "docker compose" in shell
    assert "run --rm app" in shell
    assert "python -m app.manage shell" in shell
    assert "cd backend/src" not in shell


def test_server_rendered_help_includes_selected_optional_profiles() -> None:
    help_output = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "help").stdout

    assert "build-backup-image" in help_output
    assert "verify-backup-image" in help_output
    assert "backup-image-versions" in help_output
    assert "backup-prod" in help_output
    assert "verify-backup-prod" in help_output
    assert "snapshots-prod" in help_output
    assert "restore-prod" in help_output
    assert "fe.run" not in help_output
    assert "be.run" not in help_output


def test_server_rendered_tools_explicitly_load_profile_configuration() -> None:
    profile_config = "/workspace/profiles/server-rendered-django/pyproject.toml"

    assert f"ruff check --config {profile_config}" in _make("--dry-run", "lint").stdout
    assert f"mypy --config-file {profile_config}" in _make("--dry-run", "typecheck").stdout
    test_output = _make("--dry-run", "test").stdout
    assert f"pytest -c {profile_config}" in test_output
    coverage_output = _make("--dry-run", "coverage").stdout
    assert f"pytest -c {profile_config}" in coverage_output
    assert f"--cov-config={profile_config}" in coverage_output


def test_changed_coverage_runs_from_backend() -> None:
    output = _make("--dry-run", "changed-coverage").stdout

    assert "cd /workspace/backend" in output
    assert "diff-cover" in output
    assert "coverage.xml" in output
    assert "backend/coverage.xml" not in output


@pytest.mark.skipif(shutil.which("diff-cover") is None, reason="diff-cover is profile-only")
def test_diff_cover_matches_changed_backend_application_lines(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    backend = repository / "backend"
    application = backend / "src" / "app" / "example.py"
    application.parent.mkdir(parents=True)
    application.write_text("def answer() -> int:\n    return 1\n")

    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    subprocess.run(["git", "add", "backend/src/app/example.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "baseline"], cwd=repository, check=True)

    application.write_text("def answer() -> int:\n    return 2\n")
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

    result = subprocess.run(
        ["diff-cover", "coverage.xml", "--compare-branch=HEAD", "--fail-under=90"],
        cwd=backend,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "src/app/example.py" in result.stdout
    assert "No lines with coverage information" not in result.stdout
