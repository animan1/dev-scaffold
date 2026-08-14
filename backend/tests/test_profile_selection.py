from __future__ import annotations

import os
import re
import subprocess
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
    assert _make("ci-profiles").stdout.strip().split() == ci_profiles
    assert expected_compose[selected_profile] in _make("--dry-run", "up").stdout


def test_unknown_profile_requires_its_own_implementation() -> None:
    result = _make("SCAFFOLD_PROFILE=missing", "help", check=False)

    assert result.returncode != 0
    assert "profiles/missing/profile.mk" in result.stderr


def test_ci_routes_profiles_without_duplicate_pr_push_runs() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()

    assert "branches: [main]" in workflow
    assert "make --no-print-directory ci-profiles" in workflow
    assert "contains(needs.profile-selection.outputs.ci-profiles, 'react-vite')" in workflow
    assert (
        "contains(needs.profile-selection.outputs.ci-profiles, 'server-rendered-django')"
        in workflow
    )
    server_job = workflow.split("  server-rendered-profile:", 1)[1].split("\n  backend:", 1)[0]
    assert "SCAFFOLD_PROFILE ?= server-rendered-django" in server_job
    assert "CI_PROFILES ?= server-rendered-django" in server_job
    assert (
        'test "$(make --no-print-directory ci-profiles)" = "server-rendered-django"' in server_job
    )
    assert "run: make verify" in server_job
    assert "SCAFFOLD_PROFILE:" not in server_job
    assert "pnpm" not in server_job
