from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _plan(profile: str, *targets: str) -> str:
    root = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
    return subprocess.check_output(
        ["make", "--no-print-directory", "--dry-run", f"SCAFFOLD_PROFILE={profile}", *targets],
        cwd=root,
        text=True,
    )


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_check_runs_ordered_source_gates_without_deployment(profile: str) -> None:
    output = _plan(profile, "check")
    gates = [
        "format-check",
        "lint",
        "typecheck",
        "migrations-check",
        "framework-check",
        "test COVERAGE=1",
    ]
    positions = [output.index(f"make {gate}") for gate in gates]
    assert positions == sorted(positions)
    assert "docker compose" not in output
    assert "docker build" not in output
    assert "--network none" in output
    assert "--pull=never" in output
    assert f'--user "{os.getuid()}:{os.getgid()}"' in output
    assert "deploy/source-check.env" in output
    assert "DJANGO_SETTINGS_MODULE=app.project.settings.check_prod" in output
    assert "--fix" not in output
    assert "--write" not in output
    assert ("vitest run" in output) == (profile == "react-vite")


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_source_tests_are_single_pass_and_coverage_is_explicit(profile: str) -> None:
    normal = _plan(profile, "test")
    covered = _plan(profile, "test", "COVERAGE=1", "DIFF_BASE=HEAD")
    for output in (normal, covered):
        assert output.count("-m pytest ") == 1
        assert output.count("vitest run") == (1 if profile == "react-vite" else 0)
    assert "diff-cover" not in normal
    assert "--cov=" not in normal
    assert "--cov=src" in covered
    assert "--compare-branch=HEAD" in covered
    assert "--config-file /workspace/" in covered
    assert ("coverage/lcov.info" in covered) == (profile == "react-vite")


@pytest.mark.parametrize("module", ["check", "check_prod"])
def test_source_settings_override_private_inputs(module: str) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; "
            "assert settings.DATABASES['default']['NAME'] == ':memory:'; "
            "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'; "
            "assert settings.SECRET_KEY != 'private-input'; "
            "assert settings.EMAIL_BACKEND == 'django.core.mail.backends.locmem.EmailBackend'; "
            f"assert settings.DEBUG is {module == 'check'}",
        ],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": f"app.project.settings.{module}",
            "DATABASE_URL": "postgresql://private:private@remote.invalid/private",
            "DJANGO_SECRET_KEY": "private-input",
            "DJANGO_EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        },
        check=True,
    )
