import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

REVISION = "a" * 40
SPECIFICATION = {"images": {"app": "RELEASE_BACKEND_IMAGE", "monitor": "RELEASE_BACKEND_IMAGE"}}


def image_info(**overrides: object) -> bytes:
    return json.dumps(
        [
            {
                "Id": "sha256:" + "b" * 64,
                "Os": "linux",
                "Architecture": "arm64",
                "Config": {"Labels": {"org.opencontainers.image.revision": REVISION}},
                "RepoDigests": ["example/app@sha256:" + "c" * 64],
                **overrides,
            }
        ]
    ).encode()


def test_inventories_application_database_and_optional_monitor() -> None:
    services = {
        "app": {"image": "local/app:commit"},
        "db": {"image": "postgres:16"},
        "monitor": {"image": "local/app:commit"},
    }
    with patch.object(
        bundle,
        "run",
        side_effect=[
            image_info(),
            image_info(Id="sha256:" + "d" * 64, Config={"Labels": None}, RepoDigests=[]),
            image_info(),
        ],
    ) as run:
        images = bundle.inspect_roles(
            {"services": services}, SPECIFICATION, REVISION, "linux/arm64", {}, True
        )
    assert set(images) == set(services)
    assert images["app"]["id"] == images["monitor"]["id"] == "sha256:" + "b" * 64
    assert images["db"]["id"] == "sha256:" + "d" * 64
    assert images["db"]["reference"] == "postgres:16"
    assert images["app"]["repository_digests"] == ["example/app@sha256:" + "c" * 64]
    assert all(item["platform"] == "linux/arm64" for item in images.values())
    assert [call.args for call in run.call_args_list] == [
        ("docker", "image", "inspect", service["image"]) for service in services.values()
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("Architecture", "amd64", "architecture mismatch"),
        ("Config", {"Labels": {"org.opencontainers.image.revision": "c" * 40}}, "revision label"),
        ("Config", {"Labels": None}, "revision label"),
        ("Id", "application:latest", "invalid image content ID"),
    ],
)
def test_rejects_inconsistent_cached_images(field: str, value: object, message: str) -> None:
    with patch.object(bundle, "run", return_value=image_info(**{field: value})):
        with pytest.raises(bundle.Rejected, match=message):
            bundle.inspect_roles(
                {"services": {"app": {"image": "local/app:commit"}}},
                {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                REVISION,
                "linux/arm64",
                {},
                True,
            )


def test_rejects_missing_adapter_service_before_inspecting() -> None:
    with patch.object(bundle, "run") as run:
        with pytest.raises(bundle.Rejected, match="adapter image service missing"):
            bundle.inspect_roles({"services": {}}, SPECIFICATION, REVISION, "linux/arm64", {}, True)
        run.assert_not_called()


def test_every_runtime_service_requires_an_image() -> None:
    with patch.object(bundle, "run") as run:
        with pytest.raises(bundle.Rejected, match="every runtime service"):
            bundle.inspect_roles(
                {"services": {"db": {}}}, {"images": {}}, REVISION, "linux/arm64", {}, True
            )
        run.assert_not_called()


def test_missing_cached_image_does_not_trigger_a_pull() -> None:
    with patch.object(
        bundle,
        "run",
        side_effect=subprocess.CalledProcessError(
            1, ["docker", "image", "inspect", "missing:commit"]
        ),
    ) as run:
        with pytest.raises(subprocess.CalledProcessError):
            bundle.inspect_roles(
                {"services": {"app": {"image": "missing:commit"}}},
                {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                REVISION,
                "linux/arm64",
                {},
                True,
            )
        run.assert_called_once_with(
            "docker", "image", "inspect", "missing:commit", env={}, output=True
        )


def test_sidecar_architecture_is_checked_too() -> None:
    with patch.object(
        bundle,
        "run",
        side_effect=[image_info(), image_info(Architecture="amd64", Config={"Labels": None})],
    ):
        with pytest.raises(bundle.Rejected, match="architecture mismatch"):
            bundle.inspect_roles(
                {"services": {"app": {"image": "app:commit"}, "db": {"image": "postgres:16"}}},
                {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
                REVISION,
                "linux/arm64",
                {},
                True,
            )
