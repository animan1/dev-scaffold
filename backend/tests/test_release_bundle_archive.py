import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

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

    def test_accepts_content_verified_images_without_repository_tags(self) -> None:
        configs = bundle.validate_images(self.archive, self.images, "linux/arm64")
        self.assertEqual(configs[self.identity], self.config)

    def test_rejects_layer_tampering(self) -> None:
        self.save(b"changed layer")
        with self.assertRaisesRegex(bundle.Rejected, "layer digest"):
            bundle.validate_images(self.archive, self.images, "linux/arm64")

    def test_accepts_gzip_layers_after_verifying_uncompressed_identity(self) -> None:
        self.save(gzip.compress(self.layer))
        self.assertEqual(
            bundle.validate_images(self.archive, self.images, "linux/arm64")[self.identity],
            self.config,
        )

    def test_accepts_verified_manifest_and_index_image_identities(self) -> None:
        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "config": {"digest": self.identity},
                "layers": [{"digest": "sha256:" + hashlib.sha256(self.layer).hexdigest()}],
            }
        ).encode()
        manifest_id = "sha256:" + hashlib.sha256(manifest).hexdigest()
        index = json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {"digest": manifest_id, "platform": {"os": "linux", "architecture": "arm64"}}
                ],
            }
        ).encode()
        index_id = "sha256:" + hashlib.sha256(index).hexdigest()
        with tarfile.open(self.archive, "a") as archive:
            add(archive, "blobs/sha256/" + manifest_id[7:], manifest)
            add(archive, "blobs/sha256/" + index_id[7:], index)
        for identity in (manifest_id, index_id):
            with self.subTest(identity=identity):
                self.images["app"]["id"] = identity
                aliases: dict[str, str] = {}
                configs = bundle.validate_images(self.archive, self.images, "linux/arm64", aliases)
                self.assertEqual(configs[identity], self.config)
                self.assertEqual(aliases[identity], self.identity)

    def test_rejects_tampered_manifest_alias(self) -> None:
        manifest = json.dumps({"config": {"digest": self.identity}}).encode()
        identity = "sha256:" + hashlib.sha256(manifest).hexdigest()
        with tarfile.open(self.archive, "a") as archive:
            add(archive, "blobs/sha256/" + identity[7:], manifest + b" ")
        self.images["app"]["id"] = identity
        with self.assertRaisesRegex(bundle.Rejected, "manifest digest"):
            bundle.validate_images(self.archive, self.images, "linux/arm64")

    def test_rejects_wrong_architecture(self) -> None:
        with self.assertRaisesRegex(bundle.Rejected, "architecture"):
            bundle.validate_images(self.archive, self.images, "linux/amd64")

    def test_rejects_missing_image(self) -> None:
        images = copy.deepcopy(self.images)
        images["db"] = {**images["app"], "id": "sha256:" + "b" * 64}
        with self.assertRaisesRegex(bundle.Rejected, "incomplete"):
            bundle.validate_images(self.archive, images, "linux/arm64")

    def test_rejects_wrong_content_identity(self) -> None:
        self.images["app"]["id"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(bundle.Rejected, "incomplete"):
            bundle.validate_images(self.archive, self.images, "linux/arm64")

    def test_rejects_traversal_links_and_duplicate_entries(self) -> None:
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                with tarfile.open(self.archive, "w") as archive:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    archive.addfile(item)
                with self.assertRaises(bundle.Rejected):
                    bundle.extract(self.archive, self.root / "out")
        with tarfile.open(self.archive, "w") as archive:
            add(archive, "same", b"a")
            add(archive, "same", b"b")
        with self.assertRaises(bundle.Rejected):
            bundle.extract(self.archive, self.root / "out")
