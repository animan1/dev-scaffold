from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str) -> str:
    result = subprocess.run(
        ["make", "--dry-run", "SCAFFOLD_PROFILE=react-vite", *arguments],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.mark.parametrize(
    ("profile", "application"), [("react-vite", "backend"), ("server-rendered-django", "app")]
)
def test_production_operational_check_runs_once(profile: str, application: str) -> None:
    output = _make("ops.check-prod", f"SCAFFOLD_PROFILE={profile}")
    assert f"run --rm {application} python -m app.manage monitor_operational_integrity" in output
    assert "--watch" not in output

    external = _make("ops.check-external-prod", f"SCAFFOLD_PROFILE={profile}")
    assert "deploy/docker-compose.external-monitoring.yml --profile monitoring" in external
    assert f'MONITOR_SECRETS_DIR="{_repository_root() / "deploy/secrets"}"' in external
    assert "run --rm monitor python -m app.manage monitor_operational_integrity" in external


def test_production_monitor_target_follows_service_logs() -> None:
    assert "logs -f monitor" in _make("ops.monitor-prod")


@pytest.mark.skipif(shutil.which("docker") is None, reason="Needs Docker Compose client")
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_monitoring_config_resolves_without_a_daemon(profile: str, tmp_path: Path) -> None:
    manifest = tmp_path / "release.env"
    manifest.write_text(
        "RELEASE_BACKEND_IMAGE=local/backend:test\nRELEASE_WEB_IMAGE=local/web:test\n"
    )
    subprocess.run(
        [
            "make",
            "verify-monitoring-config",
            f"SCAFFOLD_PROFILE={profile}",
            "SCAFFOLD_BACKUP_PROFILE=none",
            "DOCKER_HOST=unix:///nonexistent",
            f"EPHEMERAL_ENV_FILE={tmp_path / 'ephemeral.env'}",
            f"RELEASE_FILE={manifest}",
        ],
        cwd=_repository_root(),
        capture_output=True,
        check=True,
        text=True,
    )
    command = _make("verify-monitoring-config", f"SCAFFOLD_PROFILE={profile}")
    assert ("-f deploy/docker-compose.external-monitoring.yml" in command) == (
        profile == "react-vite"
    )


def test_backup_status_volume_is_shared_with_operational_monitor() -> None:
    compose = (_repository_root() / "deploy/docker-compose.prod.yml").read_text()
    assert compose.count("backup_status:/backup-status:ro") == 2


def test_external_adapter_does_not_replace_monitor_service() -> None:
    root = _repository_root()
    compose = (root / "deploy/docker-compose.prod.yml").read_text()
    adapter = (root / "deploy/docker-compose.external-monitoring.yml").read_text()
    assert "  monitor:" in compose
    assert "monitor_operational_integrity" in compose
    assert "MONITOR_NOTIFICATION_BACKEND: external" in adapter
    assert not (root / "backend/src/app/deadman.py").exists()
