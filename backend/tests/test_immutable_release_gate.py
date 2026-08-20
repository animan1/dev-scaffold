from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    return Path(configured_root) if configured_root else Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("profiles", "selected", "react", "server", "passes"),
    [
        ("server-rendered-django", "server-rendered-django", "skipped", "success", True),
        ("react-vite", "react-vite", "success", "skipped", True),
        ("react-vite server-rendered-django", "react-vite", "success", "success", True),
        ("server-rendered-django", "server-rendered-django", "skipped", "failure", False),
        ("react-vite", "react-vite", "failure", "skipped", False),
        ("react-vite server-rendered-django", "react-vite", "success", "failure", False),
        ("react-vite", "server-rendered-django", "success", "skipped", False),
    ],
)
def test_immutable_release_gate_policy(
    profiles: str,
    selected: str,
    react: str,
    server: str,
    passes: bool,
) -> None:
    repository = _repository_root()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE_")
        and key not in {"COVERAGE_FILE", "COVERAGE_PROCESS_START"}
    }
    result = subprocess.run(
        [
            str(repository / "scripts/check-immutable-release-gate"),
            "--ci-profiles",
            profiles,
            "--selected-profile",
            selected,
            "--react-result",
            react,
            "--server-rendered-result",
            server,
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert (result.returncode == 0) is passes
