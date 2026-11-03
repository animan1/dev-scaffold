from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))


def dry_run(profile: str, target: str, *options: str) -> str:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            f"SCAFFOLD_PROFILE={profile}",
            target,
            *options,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_release_build_targets_platform_and_labels_exact_revision(
    profile: str, operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", profile)
    op = operator_module.Operator(tmp_path)
    revision = "a" * 40
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "head", lambda: revision)
    monkeypatch.setattr(op, "clean_source", lambda: revision)
    monkeypatch.setattr(op, "architecture", lambda: "linux/arm64")
    monkeypatch.setattr(op, "build_inputs", lambda: [])
    monkeypatch.setattr(op, "inspect_image", lambda *a: {"Id": "sha256:" + "b" * 64})

    def execute(*args: object, **kwargs: object) -> None:
        commands.append(args)
        if args[:3] == ("docker", "image", "save"):
            Path(str(args[args.index("--output") + 1])).write_bytes(b"image archive")

    monkeypatch.setattr(operator_module, "run", execute)
    monkeypatch.setattr(
        operator_module, "save_images", lambda path, identities: path.write_bytes(b"image archive")
    )
    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w"):
        pass
    monkeypatch.setattr(
        operator_module.subprocess, "check_output", lambda *a, **k: source.getvalue()
    )
    operator_module.write_json(
        tmp_path / "deploy/dependencies.json",
        {
            "images": {
                name: {"pin": "example/" + name + "@sha256:" + "c" * 64}
                for name in ("postgres", "restic", "rclone")
            }
        },
    )
    op.build()
    builds = [command for command in commands if command[:2] == ("docker", "build")]
    assert len(builds) == 3
    for command in builds:
        assert command[command.index("--platform") + 1] == "linux/arm64"
        assert (
            command[command.index("--label") + 1] == "org.opencontainers.image.revision=" + revision
        )
    manifest = operator_module.read_json(op.release_dir() / "manifest.json")
    assert {"backend", "web", "db", "backup", "tooling"} <= manifest["images"].keys()


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_normal_startup_neither_rebuilds_nor_pulls(
    profile: str, operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", profile)
    op = operator_module.Operator(tmp_path)
    data = {"revision": "a" * 40}
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "head", lambda: "a" * 40)
    monkeypatch.setattr(op, "release", lambda: data)
    monkeypatch.setattr(
        op, "config", lambda *a: (None, {"services": {"db": {}, op.app: {}, op.web: {}}})
    )
    monkeypatch.setattr(op, "preflight_release", lambda *a: None)
    monkeypatch.setattr(op, "retain", lambda *a: None)
    monkeypatch.setattr(op, "state_dir", lambda *a: tmp_path / "state")
    monkeypatch.setattr(op, "migrate", lambda *a: None)
    monkeypatch.setattr(op, "manage", lambda *a, **k: None)
    monkeypatch.setattr(op, "compose", lambda *a, **k: commands.append(a))
    op.up("prod")
    assert len(commands) == 2
    for command in commands:
        assert "--no-build" in command
        assert command[command.index("--pull") + 1] == "never"


@pytest.mark.parametrize("failure", [None, "revision", "platform"])
def test_local_manifest_pins_verified_images_or_preserves_previous_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    spec = importlib.util.spec_from_file_location(
        "record_local_release", ROOT / "scripts/record_local_release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    revision = "a" * 40
    labels = {"org.opencontainers.image.revision": revision}
    image = {
        "Id": "sha256:" + "b" * 64,
        "Os": "linux",
        "Architecture": "arm64",
        "Config": {"Labels": labels},
    }
    if failure == "revision":
        labels["org.opencontainers.image.revision"] = "c" * 40
    if failure == "platform":
        image["Architecture"] = "amd64"
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *args, **kwargs: json.dumps([image])
    )
    output = tmp_path / "release.env"
    output.write_text("previous release\n")
    options = argparse.Namespace(
        revision=revision,
        profile="react-vite",
        prefix="local/dev-scaffold",
        backend="backend:commit",
        web="web:commit",
        platform="linux/arm64",
        output=str(output),
    )
    if failure:
        with pytest.raises(ValueError):
            module.record(options)
        assert output.read_text() == "previous release\n"
    else:
        module.record(options)
        assert f"RELEASE_BACKEND_IMAGE={image['Id']}" in output.read_text()
        assert f"RELEASE_WEB_IMAGE={image['Id']}" in output.read_text()
        assert output.stat().st_mode & 0o077 == 0
