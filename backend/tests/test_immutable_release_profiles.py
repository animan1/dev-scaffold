from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize("environment", ["prod", "ephemeral"])
def test_failed_migration_never_starts_an_application(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    op = operator_module.Operator(tmp_path)
    calls: list[tuple[object, ...]] = []
    data = {
        "revision": "a" * 40,
        "platform": "linux/arm64",
        "images": {"backend": "sha256:" + "b" * 64},
    }
    monkeypatch.setattr(op, "head", lambda: "a" * 40)
    monkeypatch.setattr(op, "release", lambda: data)
    monkeypatch.setattr(
        op,
        "config",
        lambda *a: (
            None,
            {"name": "dev-scaffold-" + environment, "services": {"db": {}, "backend": {}}},
        ),
    )
    monkeypatch.setattr(
        op,
        "inspect_image",
        lambda *a: {"Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}},
    )
    monkeypatch.setattr(op, "retain", lambda *a: None)
    monkeypatch.setattr(op, "preflight_release", lambda *a: None)
    monkeypatch.setattr(op, "state_dir", lambda *a: tmp_path / "state")
    monkeypatch.setattr(op, "compose", lambda *a, **k: calls.append(a))

    def failed(*args: object) -> None:
        raise operator_module.Rejected("migration failed")

    monkeypatch.setattr(op, "migrate", failed)
    with pytest.raises(operator_module.Rejected, match="migration failed"):
        op.up(environment)
    assert len(calls) == 1
    assert calls[0][-1] == "db"
    records = list((tmp_path / "state/attempts").glob("*.json"))
    assert operator_module.read_json(records[0])["outcome"] == "failed"


def test_wrong_image_revision_is_rejected_before_database_startup(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls: list[tuple[object, ...]] = []
    data = {
        "revision": "a" * 40,
        "platform": "linux/arm64",
        "images": {"backend": "sha256:" + "b" * 64},
    }
    monkeypatch.setattr(op, "release", lambda: data)
    monkeypatch.setattr(op, "config", lambda *a: (None, {"services": {}}))
    monkeypatch.setattr(
        op,
        "inspect_image",
        lambda *a: {"Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}}},
    )
    monkeypatch.setattr(op, "clean_source", lambda: "a" * 40)
    monkeypatch.setattr(op, "native_architecture", lambda: "linux/arm64")
    monkeypatch.setattr(op, "compose", lambda *a, **k: calls.append(a))
    with pytest.raises(operator_module.Rejected, match="revision label"):
        op.up("prod")
    assert not calls


def test_runtime_inspection_uses_saved_identity_after_checkout_changes(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setattr(op, "state_dir", lambda *a: state)
    operator_module.write_json(
        state / "runtime.json",
        {"name": op.project + "-prod", "services": {"backend": {"image": "sha256:" + "b" * 64}}},
    )
    monkeypatch.setattr(
        op, "config", lambda *a: pytest.fail("must not select new checkout images for inspection")
    )
    _, model = op.current_model("prod")
    assert model["services"]["backend"]["image"] == "sha256:" + "b" * 64
