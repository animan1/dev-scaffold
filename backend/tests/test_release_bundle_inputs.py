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


def document(tmp_path: Path, **overrides: object) -> Path:
    private = tmp_path / "production.env"
    private.write_text("EXAMPLE_SECRET=preserve-this-value\n")
    private.chmod(0o600)
    value = {
        "project": "dev-scaffold",
        "env_file": str(private),
        "variables": {"HOST_INGRESS": "1", "RELEASE_BIND_ADDRESS": "127.0.0.1"},
        "smoke_url": "http://127.0.0.1:18080",
        **overrides,
    }
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(value))
    return path


def test_accepts_existing_project_and_preserves_private_configuration(tmp_path: Path) -> None:
    path = document(tmp_path)
    private = tmp_path / "production.env"
    before = private.read_bytes()
    value = bundle.inputs(path)
    assert value["project"] == "dev-scaffold"
    assert value["env_file"] == str(private)
    assert value["variables"]["RELEASE_BIND_ADDRESS"] == "127.0.0.1"
    assert private.read_bytes() == before
    assert private.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unexpected", "value", "invalid inputs fields"),
        ("project", "", "explicit Compose project"),
        ("project", "UpperCase", "explicit Compose project"),
        ("env_file", "relative.env", "absolute existing env_file"),
        ("variables", {"RELEASE_PULL": "true"}, "reserved input variable"),
        ("variables", {"COMPOSE_RELEASE": "replacement"}, "reserved input variable"),
        ("variables", {"DJANGO_SECRET_KEY": "secret"}, "unsupported input variable"),
        ("variables", {"invalid-name": "1"}, "invalid input variable name"),
        ("variables", {"HOST_INGRESS_PORT": "$(touch marker)"}, "simple Make values"),
        ("variables", {"HOST_INGRESS_PORT": "18080; touch marker"}, "simple Make values"),
        ("variables", {"HOST_INGRESS_PORT": 18080}, "simple Make values"),
        ("smoke_url", "https://example.com:443", "loopback HTTP"),
        ("smoke_url", "http://127.0.0.1:18080/path", "loopback HTTP"),
    ],
)
def test_rejects_invalid_destination_inputs(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    path = document(tmp_path, **{field: value})
    with pytest.raises(bundle.Rejected, match=message):
        bundle.inputs(path)
    assert not (tmp_path / "marker").exists()


def test_rejects_missing_or_public_environment_file(tmp_path: Path) -> None:
    path = document(tmp_path)
    private = tmp_path / "production.env"
    private.chmod(0o644)
    with pytest.raises(bundle.Rejected, match="private"):
        bundle.inputs(path)
    private.unlink()
    with pytest.raises(bundle.Rejected, match="absolute existing"):
        bundle.inputs(path)


def test_rejects_environment_path_with_make_expansion(tmp_path: Path) -> None:
    private = tmp_path / "$(untrusted).env"
    private.write_text("private\n")
    private.chmod(0o600)
    path = document(tmp_path, env_file=str(private))
    with pytest.raises(bundle.Rejected, match="shell/Make safe"):
        bundle.inputs(path)


def test_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "inputs.json"
    path.write_text('{"project":"dev-scaffold","project":"other"}')
    with pytest.raises(bundle.Rejected, match="duplicate JSON key"):
        bundle.inputs(path)
