import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    item = tarfile.TarInfo(name)
    item.size = len(data)
    archive.addfile(item, io.BytesIO(data))


class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.layer = b"example uncompressed layer"
        self.labels = {"org.opencontainers.image.revision": "a" * 40}
        self.config = {
            "os": "linux",
            "architecture": "arm64",
            "rootfs": {"diff_ids": ["sha256:" + hashlib.sha256(self.layer).hexdigest()]},
            "config": {"Labels": self.labels},
        }
        self.config_bytes = json.dumps(self.config).encode()
        self.identity = "sha256:" + hashlib.sha256(self.config_bytes).hexdigest()
        self.images = {
            "app": {
                "id": self.identity,
                "reference": "local/app:revision",
                "platform": "linux/arm64",
                "repository_digests": [],
            }
        }
        self.archive = self.root / "images.tar"
        self.save()

    def save(self, layer: bytes | None = None) -> None:
        with tarfile.open(self.archive, "w") as archive:
            add(
                archive,
                "manifest.json",
                json.dumps(
                    [
                        {
                            "Config": "config.json",
                            "Layers": ["layer.tar"],
                            "RepoTags": None,
                        }
                    ]
                ).encode(),
            )
            add(archive, "config.json", self.config_bytes)
            add(archive, "layer.tar", self.layer if layer is None else layer)

    def test_rejects_outer_checksum_before_extract(self) -> None:
        with self.assertRaisesRegex(bundle.Rejected, "SHA256 mismatch"):
            bundle.unpack(self.archive, "f" * 64, self.root / "unpack")
        self.assertFalse((self.root / "unpack").exists())

    def test_install_requires_authorization_before_any_subprocess(self) -> None:
        with (
            patch.dict(os.environ, {"EMERGENCY": "0"}),
            patch.object(bundle, "run") as run,
        ):
            with self.assertRaisesRegex(bundle.Rejected, "EMERGENCY=1"):
                bundle.destination("install")
            run.assert_not_called()

    def test_default_smoke_checks_revision_through_trusted_loopback_headers(self) -> None:
        class Response(io.BytesIO):
            status = 200

        replies = [
            Response(json.dumps({"revision": "a" * 40}).encode()),
            Response(b"root"),
            Response(b"static"),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "BUNDLE_SMOKE_URL": "http://127.0.0.1:18080",
                    "RELEASE_REVISION": "a" * 40,
                },
            ),
            patch.object(bundle.urllib.request, "urlopen", side_effect=replies) as urlopen,
        ):
            bundle.smoke()
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("X-forwarded-proto"), "https")
        self.assertEqual(request.full_url, "http://127.0.0.1:18080/api/healthz")

    def test_missing_runtime_bind_rejected_before_load(self) -> None:
        config = {
            "services": {
                "app": {
                    "image": self.identity,
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(self.root / "missing"),
                            "target": "/config",
                        }
                    ],
                }
            }
        }
        data = {"images": self.images, "mode": "hotfix", "platform": "linux/arm64"}
        with self.assertRaisesRegex(bundle.Rejected, "bind input missing"):
            bundle.frozen_config(
                config,
                data,
                {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                self.root,
                self.root / "source",
            )

    def test_pins_every_service_and_preserves_named_volumes_and_config(self) -> None:
        source = self.root / "source"
        source.mkdir()
        (source / "committed.conf").write_text("committed configuration")
        (self.root / "private.conf").write_text("existing tenant secret")
        config = {
            "services": {
                "app": {
                    "image": self.identity,
                    "build": {"context": "."},
                    "volumes": [
                        {"type": "volume", "source": "data", "target": "/data"},
                        {
                            "type": "bind",
                            "source": str(source / "committed.conf"),
                            "target": "/committed",
                        },
                        {
                            "type": "bind",
                            "source": str(source / "private.conf"),
                            "target": "/private",
                        },
                    ],
                }
            },
            "volumes": {"data": {"name": "existing-project_data"}},
        }
        data = {"images": self.images, "mode": "hotfix", "platform": "linux/arm64"}
        actual = bundle.frozen_config(
            config,
            data,
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
            self.root,
            source,
        )
        service = actual["services"]["app"]
        self.assertEqual(service["image"], self.identity)
        self.assertEqual(service["pull_policy"], "never")
        self.assertNotIn("build", service)
        self.assertEqual(service["volumes"][1]["source"], str(source / "committed.conf"))
        self.assertEqual(service["volumes"][2]["source"], str(self.root / "private.conf"))
        self.assertEqual(actual["volumes"]["data"]["name"], "existing-project_data")

    def test_changed_service_set_or_image_reference_is_rejected(self) -> None:
        data = {"images": self.images, "mode": "hotfix", "platform": "linux/arm64"}
        configs: list[dict[str, object]] = [
            {"services": {}},
            {"services": {"app": {"image": "mutable:latest"}}},
        ]
        for config in configs:
            with self.assertRaises(bundle.Rejected):
                bundle.frozen_config(
                    config,
                    data,
                    {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                    self.root,
                    self.root,
                )

    def test_json_duplicate_keys_and_manifest_expansion_are_rejected(self) -> None:
        path = self.root / "bad.json"
        path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(bundle.Rejected):
            bundle.read_json(path)
        path.write_text("RELEASE_REVISION=$(touch /tmp/unwanted)\n")
        with self.assertRaises(bundle.Rejected):
            bundle.manifest_values(path)

    def committed_bundle(
        self, change: Callable[[Path, dict[str, object]], None] | None = None
    ) -> tuple[Path, str, Path]:
        source = self.root / "repository"
        source.mkdir()
        spec = {
            "schema": 1,
            "profile": "example",
            "images": {"app": "RELEASE_BACKEND_IMAGE"},
            "check_targets": ["checks"],
        }
        bundle.write_json(source / "release-bundle.json", spec)
        (source / "deployment.conf").write_text("exact committed configuration")
        bundle.run("git", "init", "-b", "emergency-release", cwd=source, output=True)
        bundle.run("git", "add", "release-bundle.json", "deployment.conf", cwd=source)
        bundle.run(
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Committed deployment source",
            cwd=source,
            output=True,
        )
        revision = bundle.run("git", "rev-parse", "HEAD", cwd=source, output=True).decode().strip()
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "source.tar").write_bytes(
            bundle.run("git", "archive", "--format=tar", revision, cwd=source, output=True)
        )
        bundle.run(
            "git",
            "bundle",
            "create",
            str(payload / "history.bundle"),
            "refs/heads/emergency-release",
            cwd=source,
        )
        self.labels["org.opencontainers.image.revision"] = revision
        self.config_bytes = json.dumps(self.config).encode()
        self.identity = "sha256:" + hashlib.sha256(self.config_bytes).hexdigest()
        self.images["app"]["id"] = self.identity
        self.save()
        (payload / "images.tar").write_bytes(self.archive.read_bytes())
        (payload / "release.env").write_text(
            f"RELEASE_REVISION={revision}\nSCAFFOLD_PROFILE=example\n"
            f"RELEASE_BACKEND_IMAGE={self.identity}\n"
        )
        bundle.write_json(
            payload / "evidence.json",
            {
                "source": "local-checks",
                "targets": ["checks"],
                "started_at": 1,
                "passed_at": 2,
                "images": {"RELEASE_BACKEND_IMAGE": self.identity},
            },
        )
        data: dict[str, object] = {
            "schema": 1,
            "mode": "hotfix",
            "revision": revision,
            "platform": "linux/arm64",
            "profile": "example",
            "images": self.images,
        }
        if change:
            change(payload, data)
        data["files"] = {path.name: bundle.digest(path) for path in payload.iterdir()}
        bundle.write_json(payload / "bundle.json", data)
        output = self.root / "bundle.tar"
        with tarfile.open(output, "w") as archive:
            for path in payload.iterdir():
                archive.add(path, arcname=path.name)
        destination = self.root / "unpacked"
        destination.mkdir()
        return output, bundle.digest(output), destination

    def test_unpacks_exact_source_and_self_contained_hotfix_history(self) -> None:
        data, _, source = bundle.unpack(*self.committed_bundle())
        self.assertEqual(data["mode"], "hotfix")
        self.assertEqual((source / "deployment.conf").read_text(), "exact committed configuration")

    def test_rejects_revision_inconsistent_manifest(self) -> None:
        def change(payload: Path, data: dict[str, object]) -> None:
            (payload / "release.env").write_text("RELEASE_REVISION=" + "b" * 40 + "\n")

        with self.assertRaisesRegex(bundle.Rejected, "manifest revision"):
            bundle.unpack(*self.committed_bundle(change))

    def test_rejects_unvalidated_image_identity(self) -> None:
        def change(payload: Path, data: dict[str, object]) -> None:
            evidence = bundle.read_json(payload / "evidence.json")
            evidence["images"]["RELEASE_BACKEND_IMAGE"] = "sha256:" + "b" * 64
            bundle.write_json(payload / "evidence.json", evidence)

        with self.assertRaisesRegex(bundle.Rejected, "evidence images"):
            bundle.unpack(*self.committed_bundle(change))

    def test_rejects_source_that_differs_from_preserved_commit(self) -> None:
        def change(payload: Path, data: dict[str, object]) -> None:
            with tarfile.open(payload / "source.tar", "a") as archive:
                add(archive, "unexpected.conf", b"not in the commit")

        with self.assertRaisesRegex(bundle.Rejected, "differs from preserved commit"):
            bundle.unpack(*self.committed_bundle(change))


if __name__ == "__main__":
    unittest.main()


@pytest.mark.skipif(
    os.environ.get("RUN_EMERGENCY_INTEGRATION") != "1",
    reason="opt-in: privileged Docker engines and initial registry access required",
)
def test_isolated_emergency_operator_workflows() -> None:
    subprocess.run([sys.executable, str(ROOT / "tests/emergency_release/exercise.py")], check=True)
