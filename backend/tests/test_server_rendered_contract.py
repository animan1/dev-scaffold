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


def test_server_rendered_precommit_is_a_recipe() -> None:
    output = _make("--dry-run", "precommit").stdout
    assert "pre-commit run" in output
    assert "--all-files" in output
