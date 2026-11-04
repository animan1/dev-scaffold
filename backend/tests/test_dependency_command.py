from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))


@pytest.fixture
def dependency_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "dependency_commands", ROOT / "scripts/scaffold_dependencies.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_locking_refreshes_declared_images_and_uses_only_selected_container_tools(
    dependency_module: ModuleType, tmp_path: Path, profile: str
) -> None:
    path = tmp_path / "deploy/dependencies.json"
    previous = {
        "images": {
            name: {
                "source": "registry.example/" + name + ":1",
                "pin": "registry.example/" + name + ":1@sha256:" + "a" * 64,
            }
            for name in ("uv", "node")
        }
    }
    dependency_module.write_json(path, previous)
    commands: list[tuple[object, ...]] = []

    def execute(*args: object, **kwargs: object) -> str:
        commands.append(args)
        return json.dumps({"digest": "sha256:" + "b" * 64})

    dependency_module.lock_dependencies(tmp_path, profile, execute)
    updated = dependency_module.read_json(path)
    assert all(item["pin"].endswith("@sha256:" + "b" * 64) for item in updated["images"].values())
    containers = [command for command in commands if command[:2] == ("docker", "run")]
    assert len(containers) == (2 if profile == "react-vite" else 1)
    for command in containers:
        assert "--user" in command
        assert "HOME=/tmp" in command
        assert "compose" not in command
        assert any("@sha256:" in str(part) for part in command)
    assert "--entrypoint" in containers[0]
    assert "/usr/local/bin/uv" in containers[0]
    assert containers[0][-1] == (
        "backend" if profile == "react-vite" else "profiles/server-rendered-django"
    )
    if profile == "react-vite":
        assert "COREPACK_HOME=/tmp/corepack" in containers[1]
        assert containers[1][-1] == "corepack pnpm install --lockfile-only --ignore-scripts"


def test_registry_failure_preserves_previous_pins_and_does_not_resolve_packages(
    dependency_module: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "deploy/dependencies.json"
    dependency_module.write_json(
        path, {"images": {"uv": {"source": "registry.example/uv:1", "pin": "unchanged"}}}
    )
    before = path.read_bytes()

    def reject(*args: object, **kwargs: object) -> str:
        assert args[:4] == ("docker", "buildx", "imagetools", "inspect")
        raise dependency_module.Rejected("registry unavailable")

    with pytest.raises(dependency_module.Rejected, match="registry unavailable"):
        dependency_module.lock_dependencies(tmp_path, "react-vite", reject)
    assert path.read_bytes() == before
