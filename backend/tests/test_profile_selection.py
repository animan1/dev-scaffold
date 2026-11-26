from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *arguments],
        cwd=_repository_root(),
        check=check,
        capture_output=True,
        text=True,
    )


def _committed_profiles() -> tuple[str, list[str]]:
    selector = (_repository_root() / ".scaffold-profile").read_text()
    selected_match = re.search(r"^SCAFFOLD_PROFILE\s*\?=\s*(\S+)\s*$", selector, re.MULTILINE)
    ci_match = re.search(r"^CI_PROFILES\s*\?=\s*(.+?)\s*$", selector, re.MULTILINE)
    assert selected_match is not None
    assert ci_match is not None
    return selected_match.group(1), ci_match.group(1).split()


def test_committed_profile_selects_its_make_and_ci_contracts() -> None:
    selected_profile, ci_profiles = _committed_profiles()
    expected_compose = {
        "react-vite": "deploy/docker-compose.dev.yml",
        "server-rendered-django": "profiles/server-rendered-django/compose.yml",
    }

    assert selected_profile in ci_profiles
    metadata = json.loads(_make("profiles").stdout)
    assert metadata["selected"]["application"] == selected_profile
    assert metadata["selected"]["backup"] == "immutable-backup"
    assert metadata["validation"]["application"] == ci_profiles
    assert expected_compose[selected_profile] in _make("--dry-run", "up-dev").stdout


def test_unknown_profile_requires_its_own_implementation() -> None:
    result = _make("SCAFFOLD_PROFILE=missing", "SCAFFOLD_BACKUP_PROFILE=none", "help", check=False)

    assert result.returncode != 0
    assert "profiles/missing/profile.mk" in result.stderr


def test_format_commands_use_container_tooling_and_one_configuration() -> None:
    for profile in ("react-vite", "server-rendered-django"):
        output = _make(f"SCAFFOLD_PROFILE={profile}", "--dry-run", "format", "format-check").stdout
        commands = output.splitlines()
        assert len(commands) == 3
        assert all(
            "docker compose" in command and "run --rm --no-deps" in command for command in commands
        )
        assert all("--config /workspace/backend/pyproject.toml" in command for command in commands)
        assert "--fix ." in commands[0]
        assert "--check ." in commands[2]


def test_profile_configurations_apply_the_same_ruff_formatting() -> None:
    results = []
    for config in ("backend/pyproject.toml", "profiles/server-rendered-django/pyproject.toml"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--config",
                config,
                "--stdin-filename",
                "example.py",
                "-",
            ],
            cwd=_repository_root(),
            input="def example( x:int): return x+1\n",
            capture_output=True,
            text=True,
            check=True,
        )
        results.append(result.stdout)
    assert results == ["def example(x: int):\n    return x + 1\n"] * 2


def test_help_lists_commands_when_optional_makefiles_are_loaded() -> None:
    for profile in ("react-vite", "server-rendered-django"):
        for backup in ("immutable-backup", "none"):
            selectors = [f"SCAFFOLD_PROFILE={profile}", f"SCAFFOLD_BACKUP_PROFILE={backup}"]
            help_output = _make(*selectors, "help").stdout
            assert "up-prod" in help_output
            assert "prepare-prod" in help_output
            assert ("verify-backup-image" in help_output) == (backup != "none")
            assert help_output == _make(*selectors, "help-verbose").stdout
            assert help_output == _make(*selectors).stdout
            assert ("fe.setup" in help_output) == (profile == "react-vite")


def test_ci_routes_profiles_without_duplicate_pr_push_runs() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()

    assert "branches: [main]" in workflow
    assert "make --no-print-directory profiles" in workflow
    assert ".validation.application" in workflow
    assert ".selected.application" in workflow
    assert "contains(needs.profile-selection.outputs.ci-profiles, 'react-vite')" in workflow
    assert (
        "contains(needs.profile-selection.outputs.ci-profiles, 'server-rendered-django')"
        in workflow
    )
    server_job = workflow.split("  server-rendered-profile:", 1)[1].split("\n  backend:", 1)[0]
    assert "SCAFFOLD_PROFILE ?= server-rendered-django" in server_job
    assert "CI_PROFILES ?= server-rendered-django" in server_job
    assert "make --no-print-directory profiles | jq -r '.selected.application'" in server_job
    assert "run: make verify" in server_job
    assert "SCAFFOLD_PROFILE:" not in server_job
    assert "pnpm" not in server_job
