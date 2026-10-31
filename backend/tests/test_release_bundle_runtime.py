import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = "sha256:" + "a" * 64
        self.images = {
            "app": {
                "id": self.identity,
                "reference": "example/app@sha256:" + "b" * 64,
                "platform": "linux/arm64",
            }
        }

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

    def test_service_architecture_must_match_bundle(self) -> None:
        self.images["app"]["platform"] = "linux/amd64"
        with self.assertRaisesRegex(bundle.Rejected, "service platform mismatch"):
            bundle.frozen_config(
                {"services": {"app": {"image": self.identity}}},
                {"images": self.images, "mode": "hotfix", "platform": "linux/arm64"},
                {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                self.root,
                self.root,
            )

    def test_capture_pins_runtime_sidecars_as_well_as_application(self) -> None:
        self.images["db"] = {
            "id": "sha256:" + "c" * 64,
            "reference": "postgres:16",
            "platform": "linux/arm64",
        }
        actual = bundle.frozen_config(
            {
                "services": {
                    name: {"image": item["reference"]} for name, item in self.images.items()
                }
            },
            {"images": self.images, "mode": "capture", "platform": "linux/arm64"},
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
            self.root,
            self.root,
        )
        for name, service in actual["services"].items():
            self.assertEqual(service["image"], self.images[name]["id"])
            self.assertEqual(service["pull_policy"], "never")

    def test_configs_and_secrets_require_existing_private_inputs(self) -> None:
        for group in ("configs", "secrets"):
            with self.subTest(group=group):
                source = self.root / "source"
                private = self.root / "private.conf"
                private.write_text("existing secret")
                config = {
                    "services": {"app": {"image": self.identity}},
                    group: {"private": {"file": str(source / "private.conf")}},
                }
                data = {"images": self.images, "mode": "hotfix", "platform": "linux/arm64"}
                spec = {"images": {"app": "RELEASE_BACKEND_IMAGE"}}
                actual = bundle.frozen_config(config, data, spec, self.root, source)
                self.assertEqual(actual[group]["private"]["file"], str(private))
                self.assertEqual(private.read_text(), "existing secret")
                private.unlink()
                with self.assertRaisesRegex(bundle.Rejected, "config/secret missing"):
                    bundle.frozen_config(config, data, spec, self.root, source)
