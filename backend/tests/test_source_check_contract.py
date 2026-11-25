from __future__ import annotations

import os
import subprocess
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
    assert "DATABASE_URL=sqlite://:memory:" in output
    assert "--fix" not in output
    assert "--write" not in output
    assert ("vitest run" in output) == (profile == "react-vite")


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_source_tests_are_single_pass_and_coverage_is_explicit(profile: str) -> None:
    normal = _plan(profile, "test")
    covered = _plan(profile, "test", "COVERAGE=1", "DIFF_BASE=HEAD", "CHANGED_COVERAGE_MIN=93")
    for output in (normal, covered):
        assert output.count("-m pytest ") == 1
        assert output.count("vitest run") == (1 if profile == "react-vite" else 0)
    assert "diff-cover" not in normal
    assert "--cov=" not in normal
    assert "--cov=src" in covered
    assert "--compare-branch=HEAD --fail-under=93" in covered
    assert ("coverage/lcov.info" in covered) == (profile == "react-vite")
