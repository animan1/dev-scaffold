from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import ModuleType

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


def test_production_operational_check_runs_once(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "state_dir", lambda *a: tmp_path / "state")
    monkeypatch.setattr(op, "current_compose", lambda *a, **k: calls.append(a))
    op.dispatch("monitor-check-prod")
    assert len(calls) == 1
    assert calls[0][-1] == "monitor_operational_integrity"
    assert "--watch" not in calls[0]
    assert "--no-deps" in calls[0]


def test_production_monitor_target_follows_service_logs() -> None:
    assert 'scaffold.py "logs-prod-monitor"' in _make("logs-prod-monitor")


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
