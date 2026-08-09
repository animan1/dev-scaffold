from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str) -> str:
    result = subprocess.run(
        ["make", "--dry-run", *arguments],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_production_operational_check_runs_once() -> None:
    output = _make("ops.check-prod")
    assert "docker compose -f deploy/docker-compose.prod.yml" in output
    assert "run --rm backend python -m app.manage monitor_operational_integrity" in output
    assert "--watch" not in output


def test_production_monitor_target_follows_service_logs() -> None:
    assert "logs -f monitor" in _make("ops.monitor-prod")


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
