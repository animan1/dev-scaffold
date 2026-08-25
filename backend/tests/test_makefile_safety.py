from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "SCAFFOLD_PROFILE=react-vite", "PY_DIR=backend", *arguments],
        cwd=_repository_root(),
        check=check,
        capture_output=True,
        text=True,
    )


def test_precommit_runs_as_a_recipe() -> None:
    output = _make("--dry-run", "precommit").stdout
    assert "uv run --extra dev pre-commit run --all-files" in output


def test_react_profile_dependency_lock_uses_pinned_dockerized_uv() -> None:
    output = _make("--dry-run", "deps.lock").stdout

    assert "docker run --rm" in output
    assert "ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim lock" in output
    assert 'backend:/workspace"' in output
    assert "--entrypoint /usr/local/bin/uv" in output


def test_react_profile_exposes_django_migration_commands() -> None:
    migrations = _make("--dry-run", "migrations").stdout
    migrate = _make("--dry-run", "migrate").stdout

    assert "python -m app.manage makemigrations" in migrations
    assert "python -m app.manage migrate" in migrate


def test_react_profile_exposes_django_development_helpers() -> None:
    superuser = _make("--dry-run", "superuser").stdout
    shell = _make("--dry-run", "shell").stdout

    assert "python -m app.manage createsuperuser" in superuser
    assert "python -m app.manage shell" in shell


def test_down_preserves_development_volumes() -> None:
    output = _make("--dry-run", "down").stdout
    assert "docker compose -f deploy/docker-compose.dev.yml down" in output
    assert "down -v" not in output


def test_reset_requires_explicit_confirmation() -> None:
    result = _make("reset", check=False)
    assert result.returncode != 0
    assert "Refusing to remove development volumes." in result.stdout
    assert "make reset CONFIRM_RESET=1" in result.stdout


def test_confirmed_reset_removes_development_volumes() -> None:
    output = _make("--dry-run", "reset", "CONFIRM_RESET=1").stdout
    assert "docker compose -f deploy/docker-compose.dev.yml down -v" in output
