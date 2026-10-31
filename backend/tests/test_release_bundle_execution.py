import importlib.util
import json
import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def test_environment_removes_inherited_make_and_compose_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = (
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
        "MAKEFILES",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "RELEASE_BACKEND_IMAGE",
        "RELEASE_WEB_IMAGE",
    )
    for key in keys:
        monkeypatch.setenv(key, "inherited")

    env = bundle.environment()

    assert all(key not in env for key in keys)
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_environment_preserves_docker_and_tenant_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/docker.sock")
    monkeypatch.setenv("PRODUCTION_HTTP_PORT", "18080")

    env = bundle.environment()

    assert env["DOCKER_HOST"] == "unix:///tmp/docker.sock"
    assert env["PRODUCTION_HTTP_PORT"] == "18080"


def test_make_passes_values_as_arguments_and_captures_only_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(args: tuple[str, ...], **kwargs: object) -> CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, stdout=b"result")

    monkeypatch.setattr(bundle.subprocess, "run", fake_run)

    result = bundle.make(
        tmp_path,
        "release-bundle-config",
        {"RELEASE_FILE": "/tmp/manifest.json"},
        {"PATH": "/bin"},
        output=True,
    )

    assert result == b"result"
    args, kwargs = calls[0]
    assert args == (
        "make",
        "--no-print-directory",
        "-s",
        "release-bundle-config",
        "RELEASE_FILE=/tmp/manifest.json",
    )
    assert kwargs["cwd"] == tmp_path
    assert kwargs["check"] is True
    assert kwargs["stdout"] is bundle.subprocess.PIPE
    assert kwargs["env"] == {"PATH": "/bin"}


def test_options_bind_existing_project_and_release_metadata(tmp_path: Path) -> None:
    values = bundle.options(
        {"profile": "server", "revision": "a" * 40, "platform": "linux/arm64"},
        {
            "project": "tenant-prod",
            "env_file": "/private/tenant.env",
            "variables": {"PRODUCTION_HTTP_PORT": "18080"},
        },
        tmp_path / "release.env",
    )

    assert values == {
        "PRODUCTION_HTTP_PORT": "18080",
        "SCAFFOLD_PROFILE": "server",
        "RELEASE_REVISION": "a" * 40,
        "RELEASE_PLATFORM": "linux/arm64",
        "RELEASE_FILE": str(tmp_path / "release.env"),
        "PROD_ENV_FILE": "/private/tenant.env",
        "RELEASE_COMPOSE_PROJECT": "tenant-prod",
    }


def test_options_reject_project_override_that_changes_existing_identity() -> None:
    with pytest.raises(bundle.Rejected, match="project must equal"):
        bundle.options(
            {"profile": "server", "revision": "a" * 40, "platform": "linux/arm64"},
            {
                "project": "tenant-prod",
                "env_file": "/private/tenant.env",
                "variables": {"PRODUCTION_COMPOSE_PROJECT": "other-project"},
            },
            Path("/tmp/release.env"),
        )


def test_compose_checks_project_and_nonempty_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {"name": "tenant-prod", "services": {"app": {"image": "app@sha256:x"}}}
    ).encode()
    monkeypatch.setattr(bundle, "make", lambda *args, **kwargs: payload)

    result = bundle.compose(
        Path("/repo"), {"RELEASE_COMPOSE_PROJECT": "tenant-prod"}, {"PATH": "/bin"}
    )

    assert result["services"]["app"]["image"] == "app@sha256:x"


@pytest.mark.parametrize(
    "payload, message",
    [
        (b'{"name":"other","services":{"app":{}}}', "project mismatch"),
        (b'{"name":"tenant-prod","services":{}}', "empty runtime topology"),
    ],
)
def test_compose_rejects_wrong_project_or_empty_topology(
    monkeypatch: pytest.MonkeyPatch, payload: bytes, message: str
) -> None:
    monkeypatch.setattr(bundle, "make", lambda *args, **kwargs: payload)

    with pytest.raises(bundle.Rejected, match=message):
        bundle.compose(
            Path("/repo"),
            {"RELEASE_COMPOSE_PROJECT": "tenant-prod"},
            {"PATH": "/bin"},
        )
