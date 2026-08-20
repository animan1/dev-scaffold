from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _gate(
    *,
    ci_profiles: str,
    selected_profile: str,
    react_result: str,
    server_rendered_result: str,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_")
        and key not in {"COVERAGE_FILE", "COVERAGE_PROCESS_START"}
    }
    return subprocess.run(
        [
            str(_repository_root() / "scripts/check-immutable-release-gate"),
            "--ci-profiles",
            ci_profiles,
            "--selected-profile",
            selected_profile,
            "--react-result",
            react_result,
            "--server-rendered-result",
            server_rendered_result,
        ],
        cwd=_repository_root(),
        env=environment,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("ci_profiles", "selected_profile", "react_result", "server_result"),
    [
        ("server-rendered-django", "server-rendered-django", "skipped", "success"),
        ("react-vite", "react-vite", "success", "skipped"),
        ("react-vite server-rendered-django", "react-vite", "success", "success"),
    ],
)
def test_release_gate_accepts_selected_profile_combinations(
    ci_profiles: str,
    selected_profile: str,
    react_result: str,
    server_result: str,
) -> None:
    result = _gate(
        ci_profiles=ci_profiles,
        selected_profile=selected_profile,
        react_result=react_result,
        server_rendered_result=server_result,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("ci_profiles", "selected_profile", "react_result", "server_result"),
    [
        ("server-rendered-django", "server-rendered-django", "skipped", "failure"),
        ("react-vite", "react-vite", "failure", "skipped"),
        ("react-vite server-rendered-django", "react-vite", "success", "failure"),
    ],
)
def test_release_gate_rejects_failed_selected_profile_verification(
    ci_profiles: str,
    selected_profile: str,
    react_result: str,
    server_result: str,
) -> None:
    result = _gate(
        ci_profiles=ci_profiles,
        selected_profile=selected_profile,
        react_result=react_result,
        server_rendered_result=server_result,
    )

    assert result.returncode == 1
    assert "Required profile verification failed" in result.stderr


def test_release_gate_rejects_a_selected_profile_missing_from_ci() -> None:
    result = _gate(
        ci_profiles="react-vite",
        selected_profile="server-rendered-django",
        react_result="success",
        server_rendered_result="skipped",
    )

    assert result.returncode == 1
    assert "not included in CI_PROFILES" in result.stderr
