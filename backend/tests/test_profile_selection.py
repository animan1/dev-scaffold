from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


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


def test_changed_coverage_uses_shared_default_and_legacy_branch_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIFF_BASE", raising=False)
    for profile in ("react-vite", "server-rendered-django"):
        selectors = [f"SCAFFOLD_PROFILE={profile}", "--dry-run", "coverage-in-container"]
        assert "--compare-branch=origin/main" in _make(*selectors).stdout
        legacy = _make(*selectors, "DIFF_BASE=origin/master").stdout
        assert "--compare-branch=origin/master" in legacy
        assert "--compare-branch=origin/main" not in legacy


def test_format_commands_use_container_tooling_and_one_configuration() -> None:
    for profile in ("react-vite", "server-rendered-django"):
        output = _make(f"SCAFFOLD_PROFILE={profile}", "--dry-run", "format", "format-check").stdout
        commands = output.splitlines()
        assert len(commands) == 3
        assert all(
            "docker compose" in command and "run --rm --no-deps" in command for command in commands
        )
        assert all("--config /workspace/backend/pyproject.toml" in command for command in commands)
        project = (
            "/app" if profile == "react-vite" else "/workspace/profiles/server-rendered-django"
        )
        assert all(f"uv run --project {project} ruff" in command for command in commands)
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


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize("django_fails", [False, True])
def test_common_lint_runs_frontend_only_when_applicable_and_django_passes(
    profile: str, django_fails: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "lint.log"
    for tool, body in {
        "docker": (
            'case "$*" in *" frontend corepack pnpm lint --max-warnings 0")\n'
            '  echo frontend >> "$LINT_LOG"; exit 0 ;;\n'
            'esac\necho django >> "$LINT_LOG"\nexit "$DJANGO_LINT_STATUS"\n'
        ),
        "pnpm": 'echo host-pnpm >> "$LINT_LOG"\nexit 99\n',
    }.items():
        path = tmp_path / tool
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("LINT_LOG", str(log))
    monkeypatch.setenv("DJANGO_LINT_STATUS", str(int(django_fails)))
    result = _make(f"SCAFFOLD_PROFILE={profile}", "lint", check=False)
    assert (result.returncode != 0) == django_fails
    expected = ["django"]
    if profile == "react-vite" and not django_fails:
        expected.append("frontend")
    assert log.read_text().splitlines() == expected


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_ci_checks_source_without_starting_dev_services(profile: str) -> None:
    output = _make(f"SCAFFOLD_PROFILE={profile}", "--dry-run", "ci-ephemeral").stdout

    assert output.count("--no-deps --env DATABASE_URL= ") == 2
    assert "up-dev" not in output
    assert "down-dev" not in output
    assert "up-prod" not in output
    assert "coverage-in-container" in output
    assert ("diff-cover coverage/lcov.info" in output) == (profile == "react-vite")
    assert ("frontend corepack pnpm fmt-check" in output) == (profile == "react-vite")
    assert "up-ephemeral HOST_INGRESS=0" in output
    assert "verify-ephemeral HOST_INGRESS=0" in output
    assert "down -v --remove-orphans" in output


@pytest.mark.parametrize(
    "failure",
    ["", "check", "up-ephemeral", "verify-ephemeral", "integration-test-ephemeral", "rm-ephemeral"],
)
def test_ci_cleans_up_release_validation_and_reports_failure(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make = tmp_path / "record-make"
    log = tmp_path / "calls"
    make.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$CALL_LOG"\n[ "$1" != "$FAIL_TARGET" ]\n')
    make.chmod(0o755)
    monkeypatch.setenv("CALL_LOG", str(log))
    monkeypatch.setenv("FAIL_TARGET", failure)

    result = _make(f"MAKE={make}", "ci-ephemeral", check=False)

    expected = ["setup", "check"]
    if failure != "check":
        expected += ["build-release-images", "up-ephemeral"]
        if failure != "up-ephemeral":
            expected.append("verify-ephemeral")
            if failure != "verify-ephemeral":
                expected.append("integration-test-ephemeral")
        expected.append("rm-ephemeral")
    assert log.read_text().splitlines() == expected
    assert (result.returncode != 0) == bool(failure)


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize("python_fails", [False, True])
def test_typecheck_runs_frontend_only_when_applicable_and_python_passes(
    profile: str, python_fails: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "typecheck.log"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\ncase "$*" in *" frontend corepack pnpm typecheck")\n'
        '  echo frontend >> "$TYPECHECK_LOG"; exit 0 ;;\n'
        'esac\necho python >> "$TYPECHECK_LOG"\nexit "$PYTHON_TYPECHECK_STATUS"\n'
    )
    docker.chmod(0o755)
    pnpm = tmp_path / "pnpm"
    pnpm.write_text('#!/bin/sh\necho host-pnpm >> "$TYPECHECK_LOG"\nexit 99\n')
    pnpm.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("TYPECHECK_LOG", str(log))
    monkeypatch.setenv("PYTHON_TYPECHECK_STATUS", str(int(python_fails)))
    result = _make(f"SCAFFOLD_PROFILE={profile}", "typecheck", check=False)
    assert (result.returncode != 0) == python_fails
    expected = ["python"]
    if profile == "react-vite" and not python_fails:
        expected.append("frontend")
    assert log.read_text().splitlines() == expected


@pytest.mark.parametrize(
    ("profile", "service"), [("react-vite", "backend"), ("server-rendered-django", "app")]
)
def test_setup_builds_development_dependencies(profile: str, service: str) -> None:
    output = _make(f"SCAFFOLD_PROFILE={profile}", "--dry-run", "setup").stdout

    assert f"build {service}\n" in output
    assert " up " not in output
    assert ("fe.setup-in-container" in output) == (profile == "react-vite")


def test_react_setup_and_ci_share_the_container_install() -> None:
    selectors = ("SCAFFOLD_PROFILE=react-vite", "--dry-run")
    ci = _make(*selectors, "fe.coverage").stdout
    install = _make(*selectors, "fe.setup-in-container").stdout

    assert "COVERAGE=1 fe.setup-in-container fe.test-in-container" in ci
    assert install.strip() == "corepack pnpm install --frozen-lockfile"


def test_help_lists_commands_when_optional_makefiles_are_loaded() -> None:
    for profile in ("react-vite", "server-rendered-django"):
        for backup in ("immutable-backup", "none"):
            selectors = [f"SCAFFOLD_PROFILE={profile}", f"SCAFFOLD_BACKUP_PROFILE={backup}"]
            help_output = _make(*selectors, "help").stdout
            assert "up-prod" in help_output
            assert "prepare-prod" in help_output
            assert "\x1b[36msetup " in help_output
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
    assert "run: make ci-ephemeral" in server_job
    assert "run: make ci-ephemeral SCAFFOLD_PROFILE=react-vite" in workflow
    assert "run: make up-dev" not in workflow
    assert "SCAFFOLD_PROFILE:" not in server_job
    assert "pnpm" not in server_job
