import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))


def recorder(tmp_path: Path) -> Path:
    command = tmp_path / "record-command"
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
    command.chmod(0o755)
    return command


@pytest.mark.parametrize(
    "profile, quality",
    [("react-vite", "be.ci fe.ci"), ("server-rendered-django", "build check")],
)
def test_hotfix_hook_does_not_start_or_stop_the_development_stack(
    tmp_path: Path, profile: str, quality: str
) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "release-bundle-check",
            f"SCAFFOLD_PROFILE={profile}",
            f"MAKE={recorder(tmp_path)}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        quality,
        "verify-release-images verify-host-ingress verify-monitoring-config",
        "down-release-ci",
    ]


@pytest.mark.parametrize("target, service", [("be.ci", "backend"), ("fe.ci", "frontend")])
def test_quality_containers_do_not_start_dependencies_or_publish_ports(
    tmp_path: Path, target: str, service: str
) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            target,
            "SCAFFOLD_PROFILE=react-vite",
            f"COMPOSE_DEV={recorder(tmp_path)}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = result.stdout.strip()
    assert command.startswith("run ")
    assert f"--no-deps {service} " in command
    assert "--rm" in command
    assert "--service-ports" not in command
    assert " up " not in command
