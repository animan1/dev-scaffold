import hashlib
import importlib.util
import json
import os
import tarfile
from pathlib import Path

import pytest
import test_release_bundle_source as history
from test_release_bundle_archive import add
from test_release_bundle_source import git

preserved_source = history.preserved_source

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


@pytest.fixture
def payload(preserved_source: tuple[Path, Path, str]) -> tuple[Path, str, str]:
    repository, work, _ = preserved_source
    bundle.write_json(
        repository / "release-bundle.json",
        {
            "schema": 1,
            "profile": "example",
            "images": {"app": "RELEASE_BACKEND_IMAGE"},
            "check_targets": ["checks"],
        },
    )
    git("add", "release-bundle.json", cwd=repository)
    git("commit", "-m", "Commit release adapter", cwd=repository)
    revision = git("rev-parse", "HEAD", cwd=repository).decode().strip()
    (work / "source.tar").write_bytes(git("archive", "--format=tar", revision, cwd=repository))
    git("bundle", "create", str(work / "history.bundle"), "refs/heads/hotfix", cwd=repository)
    layer = b"runtime layer"
    config = json.dumps(
        {
            "os": "linux",
            "architecture": "arm64",
            "rootfs": {"diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]},
            "config": {"Labels": {"org.opencontainers.image.revision": revision}},
        }
    ).encode()
    identity = "sha256:" + hashlib.sha256(config).hexdigest()
    with tarfile.open(work / "images.tar", "w") as archive:
        add(
            archive,
            "manifest.json",
            json.dumps(
                [{"Config": "config.json", "Layers": ["layer.tar"], "RepoTags": None}]
            ).encode(),
        )
        add(archive, "config.json", config)
        add(archive, "layer.tar", layer)
    (work / "release.env").write_text(
        f"RELEASE_REVISION={revision}\nSCAFFOLD_PROFILE=example\nRELEASE_BACKEND_IMAGE={identity}\n"
    )
    bundle.write_json(
        work / "evidence.json",
        {
            "source": "local-checks",
            "targets": ["checks"],
            "started_at": 1,
            "passed_at": 2,
            "images": {"RELEASE_BACKEND_IMAGE": identity},
        },
    )
    return work, revision, identity


def pack(
    payload: tuple[Path, str, str], mode: str = "hotfix", **overrides: object
) -> tuple[Path, str, Path]:
    work, revision, identity = payload
    reference = "example/app@sha256:" + "b" * 64
    if mode == "capture":
        (work / "release.env").write_text(
            f"RELEASE_REVISION={revision}\nSCAFFOLD_PROFILE=example\nRELEASE_BACKEND_IMAGE={reference}\n"
        )
        with tarfile.open(work / "artifacts.tar", "w"):
            pass
        bundle.write_json(
            work / "evidence.json",
            {
                "source": "github-main",
                "run": {"head_sha": revision, "head_branch": "main", "conclusion": "success"},
            },
        )
    bundle.write_json(
        work / "bundle.json",
        {
            "schema": 1,
            "mode": mode,
            "revision": revision,
            "platform": "linux/arm64",
            "profile": "example",
            "images": {"app": {"id": identity, "reference": reference, "platform": "linux/arm64"}},
            "files": {path.name: bundle.digest(path) for path in work.iterdir()},
            **overrides,
        },
    )
    output = work.parent / "bundle.tar"
    with tarfile.open(output, "w") as archive:
        for path in work.iterdir():
            archive.add(path, arcname=path.name)
    return output, bundle.digest(output), work.parent / "unpacked"


@pytest.mark.parametrize("mode", ["hotfix", "capture"])
def test_validates_portable_release(payload: tuple[Path, str, str], mode: str) -> None:
    data, spec, source = bundle.unpack(*pack(payload, mode))
    assert data["mode"] == mode
    assert data["revision"] == payload[1]
    assert spec["check_targets"] == ["checks"]
    assert (source / "deployment.conf").read_text() == "exact committed deployment input\n"


def test_rejects_outer_checksum_before_extraction(payload: tuple[Path, str, str]) -> None:
    output, _, destination = pack(payload)
    with pytest.raises(bundle.Rejected, match="SHA256 mismatch"):
        bundle.unpack(output, "f" * 64, destination)
    assert not destination.exists()


@pytest.mark.parametrize("checksum", ["", "short"])
def test_requires_independent_checksum(payload: tuple[Path, str, str], checksum: str) -> None:
    output, _, destination = pack(payload)
    with pytest.raises(bundle.Rejected, match="independently supplied"):
        bundle.unpack(output, checksum, destination)
    assert not destination.exists()


def test_rejects_missing_runtime_images(payload: tuple[Path, str, str]) -> None:
    (payload[0] / "images.tar").unlink()
    with pytest.raises(bundle.Rejected, match="incomplete"):
        bundle.unpack(*pack(payload))


def test_rejects_wrong_architecture(payload: tuple[Path, str, str]) -> None:
    with pytest.raises(bundle.Rejected, match="architecture"):
        bundle.unpack(*pack(payload, platform="linux/amd64"))


def test_rejects_manifest_revision_mismatch(payload: tuple[Path, str, str]) -> None:
    (payload[0] / "release.env").write_text("RELEASE_REVISION=" + "c" * 40 + "\n")
    with pytest.raises(bundle.Rejected, match="manifest revision"):
        bundle.unpack(*pack(payload))


def test_rejects_unvalidated_images(payload: tuple[Path, str, str]) -> None:
    path = payload[0] / "evidence.json"
    evidence = bundle.read_json(path)
    evidence["images"]["RELEASE_BACKEND_IMAGE"] = "sha256:" + "d" * 64
    bundle.write_json(path, evidence)
    with pytest.raises(bundle.Rejected, match="evidence images"):
        bundle.unpack(*pack(payload))


def test_rejects_substituted_source(payload: tuple[Path, str, str]) -> None:
    with tarfile.open(payload[0] / "source.tar", "a") as archive:
        add(archive, "unexpected.conf", b"not committed")
    with pytest.raises(bundle.Rejected, match="differs from preserved commit"):
        bundle.unpack(*pack(payload))
