import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def test_accepts_exact_release_identifiers() -> None:
    assert bundle.revision("a" * 40) == "a" * 40
    assert bundle.platform("linux/arm64") == "linux/arm64"
    assert bundle.platform("linux/amd64") == "linux/amd64"
    assert bundle.image_id("sha256:" + "b" * 64) == "sha256:" + "b" * 64


@pytest.mark.parametrize(
    ("function", "value"),
    [
        ("revision", "main"),
        ("revision", "a" * 12),
        ("revision", "A" * 40),
        ("platform", "linux/arm/v7"),
        ("platform", ""),
        ("image_id", "application:latest"),
        ("image_id", "sha256:" + "b" * 12),
    ],
)
def test_rejects_ambiguous_or_unsupported_identifiers(function: str, value: str) -> None:
    with pytest.raises(bundle.Rejected):
        getattr(bundle, function)(value)


@pytest.mark.parametrize(
    ("profile", "backend", "web"),
    [
        ("react-vite", "backend", "nginx"),
        ("server-rendered-django", "app", "web"),
    ],
)
def test_default_adapters_map_existing_profile_services(
    profile: str, backend: str, web: str
) -> None:
    value = bundle.adapter(ROOT, profile)
    assert value["images"][backend] == "RELEASE_BACKEND_IMAGE"
    assert value["images"][web] == "RELEASE_WEB_IMAGE"
    assert value["images"]["monitor"] == "RELEASE_BACKEND_IMAGE"
    assert value["check_targets"] == ["release-bundle-check"]


def tenant_adapter(tmp_path: Path, **overrides: object) -> Path:
    value = {
        "schema": 1,
        "profile": "tenant",
        "images": {"app": "RELEASE_BACKEND_IMAGE"},
        "check_targets": ["be.verify", "verify-release-images"],
        **overrides,
    }
    path = tmp_path / "release-bundle.json"
    path.write_text(json.dumps(value))
    return path


def test_tenant_adapter_takes_precedence_over_profile_fallback(tmp_path: Path) -> None:
    tenant_adapter(tmp_path)
    value = bundle.adapter(tmp_path, "tenant")
    assert value["images"] == {"app": "RELEASE_BACKEND_IMAGE"}
    assert value["check_targets"] == ["be.verify", "verify-release-images"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2),
        ("profile", "different"),
        ("unexpected", "field"),
        ("images", {}),
        ("images", {"app": "application:latest"}),
        ("images", {"invalid service": "RELEASE_BACKEND_IMAGE"}),
        ("check_targets", []),
        ("check_targets", ["$(touch marker)"]),
    ],
)
def test_rejects_invalid_tenant_adapters(tmp_path: Path, field: str, value: object) -> None:
    tenant_adapter(tmp_path, **{field: value})
    with pytest.raises(bundle.Rejected):
        bundle.adapter(tmp_path, "tenant")
    assert not (tmp_path / "marker").exists()


def test_reads_manifest_without_shell_or_make_expansion(tmp_path: Path) -> None:
    path = tmp_path / "release.env"
    path.write_text(
        "RELEASE_REVISION=" + "a" * 40 + "\nRELEASE_BACKEND_IMAGE=sha256:" + "b" * 64 + "\n"
    )
    values = bundle.manifest_values(path)
    assert values["RELEASE_REVISION"] == "a" * 40
    assert values["RELEASE_BACKEND_IMAGE"] == "sha256:" + "b" * 64


@pytest.mark.parametrize(
    "content",
    [
        "RELEASE_REVISION=first\nRELEASE_REVISION=second\n",
        "RELEASE_REVISION=$(touch marker)\n",
        "RELEASE_REVISION=first;touch marker\n",
        "MISSING_SEPARATOR\n",
    ],
)
def test_rejects_unsafe_or_ambiguous_manifests(tmp_path: Path, content: str) -> None:
    path = tmp_path / "release.env"
    path.write_text(content)
    with pytest.raises(bundle.Rejected):
        bundle.manifest_values(path)
    assert not (tmp_path / "marker").exists()
