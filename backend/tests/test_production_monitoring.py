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


def test_production_monitor_target_follows_logs() -> None:
    output = _make("ops.monitor-prod")

    assert "logs -f monitor" in output
