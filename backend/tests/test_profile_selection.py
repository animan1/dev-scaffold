from __future__ import annotations

import json
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
    assert selected_profile in ci_profiles
    metadata = json.loads(_make("profiles").stdout)
    assert metadata["selected"]["application"] == selected_profile
    assert metadata["selected"]["backup"] == "none"
    assert metadata["validation"]["application"] == ci_profiles
    assert 'scaffold.py "up-dev"' in _make("--dry-run", "up-dev").stdout


def test_unknown_profile_requires_its_own_implementation() -> None:
    result = _make("SCAFFOLD_PROFILE=missing", "help", check=False)

    assert result.returncode != 0
    assert "unknown selected scaffold profile" in result.stderr


def test_ci_routes_profiles_without_duplicate_pr_push_runs() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()

    assert "branches: [main]" in workflow
    assert "make --no-print-directory profiles" in workflow
    assert ".validation.application" in workflow
    assert ".selected.application" in workflow
    assert "for profile in $APPLICATIONS" in workflow
    assert "for backup in $BACKUPS" in workflow
    assert (
        'make SCAFFOLD_PROFILE="$profile" SCAFFOLD_BACKUP_PROFILE="$backup" ci-ephemeral'
        in workflow
    )
    assert "make publish" in workflow
    assert workflow.index("ci-ephemeral") < workflow.index("make publish")
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "> .scaffold-profile" not in workflow
    assert "pnpm" not in workflow
