from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def test_legacy_and_host_native_commands_are_not_public() -> None:
    root = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))
    for name in ("up", "reset", "be.run", "fe.run", "precommit", "wait", "deploy-release"):
        result = subprocess.run(["make", "--dry-run", name], cwd=root, capture_output=True)
        assert result.returncode != 0, name


@pytest.mark.parametrize("environment", ["dev", "prod", "ephemeral"])
def test_normal_stop_never_removes_data(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    op = operator_module.Operator(tmp_path)
    calls = []
    monkeypatch.setattr(op, "current_model", lambda *a: (None, {"services": {"backend": {}}}))
    monkeypatch.setattr(op, "current_compose", lambda *a, **k: calls.append(a))
    op.down(environment)
    assert calls == [(environment, "stop")]


def test_unknown_selector_never_reaches_a_mutating_command(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls = []
    monkeypatch.setattr(op, "current_model", lambda *a: (None, {"services": {"backend": {}}}))
    monkeypatch.setattr(op, "current_compose", lambda *a, **k: calls.append(a))
    with pytest.raises(operator_module.Rejected, match="unknown service"):
        op.down("prod", "typo")
    assert not calls


def test_cleanup_rejects_another_environments_project(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls = []
    monkeypatch.setattr(op, "config", lambda *a: (None, {"name": "dev-scaffold-prod"}))
    monkeypatch.setattr(op, "compose", lambda *a, **k: calls.append(a))
    with pytest.raises(operator_module.Rejected, match="namespace"):
        op.remove("ephemeral")
    assert not calls


def test_database_only_start_does_not_migrate_or_touch_other_services(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls = []
    monkeypatch.setattr(op, "config", lambda *a: (None, {"services": {"db": {}, "backend": {}}}))
    monkeypatch.setattr(op, "compose", lambda *a, **k: calls.append(a))
    op.up("dev", "db")
    assert calls == [("dev", "up", "--detach", "--no-build", "--no-deps", "--wait", "db")]


@pytest.mark.parametrize("command", ["restore", "rollback"])
def test_data_replacement_requires_per_invocation_confirmation(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    op = operator_module.Operator(tmp_path)
    monkeypatch.delenv("CONFIRM", raising=False)
    with pytest.raises(operator_module.Rejected, match="CONFIRM"):
        getattr(op, command)("prod")


def test_failed_backup_prevents_migration(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls = []
    monkeypatch.setattr(op, "compose", lambda *a, **k: None)
    monkeypatch.setattr(op, "database_state", lambda *a: {"status": "ready"})
    monkeypatch.setattr(op, "manage", lambda *a, **k: calls.append(a))

    def failed(*args: object, **kwargs: object) -> None:
        raise operator_module.Rejected("backup failed")

    monkeypatch.setattr(op, "backup", failed)
    with pytest.raises(operator_module.Rejected, match="backup failed"):
        op.migrate("dev")
    assert not calls
