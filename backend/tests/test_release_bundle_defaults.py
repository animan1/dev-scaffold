import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import test_release_bundle_preflight as preflight
import test_release_bundle_source as history
from test_release_bundle_source import git

preserved_source = history.preserved_source
payload = preflight.payload

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
REVISION = "a" * 40


@pytest.mark.parametrize("operation", ["capture", "hotfix", "validate", "install"])
def test_make_exports_normal_config_without_required_bundle_parameters(
    tmp_path: Path, operation: str
) -> None:
    python = tmp_path / "python3"
    python.write_text(f"#!{sys.executable}\nimport json, os\nprint(json.dumps(dict(os.environ)))\n")
    python.chmod(0o755)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("COV_CORE_", "COVERAGE_")):
            env.pop(key)
    for key in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "RELEASE_REVISION"):
        env.pop(key, None)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            f"release-bundle-{operation}",
            "RELEASE_COMPOSE_PROJECT=existing-project",
            "PROD_ENV_FILE=/private/normal.env",
            "RELEASE_HTTP_PORT=19080",
            "MONITORING=1",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    exported = json.loads(result.stdout)
    assert exported["RELEASE_COMPOSE_PROJECT"] == "existing-project"
    assert exported["PROD_ENV_FILE"] == "/private/normal.env"
    assert exported["RELEASE_HTTP_PORT"] == "19080"
    assert exported["MONITORING"] == "1"
    assert not exported["BUNDLE_INPUTS"]
    assert not exported["BUNDLE"]
    if operation == "capture":
        assert not exported["RELEASE_REVISION"]  # Make's HEAD default must not select capture.
    else:
        assert exported["RELEASE_REVISION"] == git("rev-parse", "HEAD", cwd=ROOT).decode().strip()


def test_inputs_reuse_normal_configuration_and_recorded_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "existing.env"
    env_file.write_text("DATABASE_PASSWORD=secret\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("PROD_ENV_FILE", str(env_file))
    monkeypatch.setenv("RELEASE_COMPOSE_PROJECT", "normal-default")
    monkeypatch.setenv("BUNDLE_EXPLICIT_PROJECT", "")
    monkeypatch.setenv("RELEASE_HTTP_PORT", "19080")
    record = tmp_path / ".git/tenant-deployment.json"
    record.parent.mkdir()
    record.write_text('{"project":"existing-project"}')

    settings = bundle.inputs()
    assert settings["project"] == "existing-project"
    assert settings["env_file"] == str(env_file)
    assert settings["variables"]["RELEASE_HTTP_PORT"] == "19080"
    assert settings["smoke_url"] == "http://127.0.0.1:19080"
    assert "secret" not in json.dumps(settings)
    monkeypatch.setenv("BUNDLE_EXPLICIT_PROJECT", "1")
    assert bundle.inputs()["project"] == "normal-default"


@pytest.mark.parametrize(
    "remote", ["git@github.com:owner/app.git", "https://github.com/owner/app.git"]
)
def test_capture_detects_latest_successful_run_revision_repo_and_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote: str
) -> None:
    for key in (
        "BUNDLE_REPO",
        "BUNDLE_RUN_ID",
        "BUNDLE_ARTIFACT",
        "RELEASE_REVISION",
        "BUNDLE_WORKFLOW",
    ):
        monkeypatch.delenv(key, raising=False)
    calls: list[tuple[str, ...]] = []

    def run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if args[0] == "git":
            return remote.encode()
        if "workflows/ci.yml/runs?" in args[2]:
            return json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 42,
                            "head_sha": REVISION,
                            "head_branch": "main",
                            "event": "push",
                            "status": "completed",
                            "conclusion": "success",
                            "repository": {"full_name": "owner/app"},
                        }
                    ]
                }
            ).encode()
        assert args[2] == "repos/owner/app/actions/runs/42/artifacts?per_page=100"
        return json.dumps(
            {
                "artifacts": [
                    {
                        "name": f"release-{REVISION}-arm64",
                        "expired": False,
                    }
                ]
            }
        ).encode()

    monkeypatch.setattr(bundle, "run", run)
    selected = bundle.capture_selection(tmp_path, "linux/arm64", {})
    assert selected == {
        "RELEASE_REVISION": REVISION,
        "BUNDLE_REPO": "owner/app",
        "BUNDLE_RUN_ID": "42",
        "BUNDLE_ARTIFACT": f"release-{REVISION}-arm64",
    }
    assert "branch=main&event=push&status=success&per_page=1" in calls[1][2]
    assert len(calls) == 3


def test_capture_does_not_fall_back_to_an_older_run_when_artifact_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BUNDLE_REPO", "owner/app")
    monkeypatch.setenv("BUNDLE_RUN_ID", "42")
    monkeypatch.delenv("BUNDLE_ARTIFACT", raising=False)
    monkeypatch.delenv("RELEASE_REVISION", raising=False)
    calls: list[tuple[str, ...]] = []

    def run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if "/artifacts?" in args[2]:
            return b'{"artifacts":[]}'
        return json.dumps(
            {
                "head_sha": REVISION,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": "owner/app"},
            }
        ).encode()

    monkeypatch.setattr(bundle, "run", run)
    with pytest.raises(bundle.Rejected, match="no older release selected"):
        bundle.capture_selection(tmp_path, "linux/arm64", {})
    assert len(calls) == 2
    assert not any("workflows" in call[2] for call in calls)


@pytest.mark.parametrize("requested_revision", [REVISION, "b" * 40])
def test_capture_honors_explicit_run_and_checks_revision_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, requested_revision: str
) -> None:
    artifact = f"release-{REVISION}-arm64"
    monkeypatch.setenv("BUNDLE_REPO", "owner/app")
    monkeypatch.setenv("BUNDLE_RUN_ID", "42")
    monkeypatch.setenv("BUNDLE_ARTIFACT", artifact)
    monkeypatch.setenv("RELEASE_REVISION", requested_revision)
    calls: list[tuple[str, ...]] = []

    def run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        assert args == ("gh", "api", "repos/owner/app/actions/runs/42")
        return json.dumps(
            {
                "head_sha": REVISION,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": "Owner/App"},
            }
        ).encode()

    monkeypatch.setattr(bundle, "run", run)
    if requested_revision != REVISION:
        with pytest.raises(bundle.Rejected, match="differs from RELEASE_REVISION"):
            bundle.capture_selection(tmp_path, "linux/arm64", {})
    else:
        selected = bundle.capture_selection(tmp_path, "linux/arm64", {})
        assert selected["BUNDLE_ARTIFACT"] == artifact
        assert selected["RELEASE_REVISION"] == REVISION
    assert len(calls) == 1


def test_dirty_hotfix_rejects_before_docker_or_source_packaging(
    monkeypatch: pytest.MonkeyPatch, preserved_source: tuple[Path, Path, str]
) -> None:
    repository, _, _ = preserved_source
    monkeypatch.chdir(repository)
    monkeypatch.delenv("BUNDLE", raising=False)
    (repository / "deployment.conf").write_text("uncommitted edit\n")
    calls: list[tuple[str, ...]] = []
    original_run = bundle.run

    def run(*args: str, **kwargs: object) -> bytes | None:
        calls.append(args)
        result = original_run(*args, **kwargs)
        assert result is None or isinstance(result, bytes)
        return result

    monkeypatch.setattr(bundle, "run", run)
    with pytest.raises(bundle.Rejected, match="commit all hotfix changes first"):
        bundle.package("hotfix")
    assert calls == [("git", "status", "--porcelain")]


def test_bare_hotfix_packages_local_head_and_installs_using_normal_settings(
    monkeypatch: pytest.MonkeyPatch, payload: tuple[Path, str, str]
) -> None:
    work, revision, identity = payload
    repository = work.parent / "repository"
    (repository / ".git/info/exclude").write_text(".local/\n")
    monkeypatch.chdir(repository)
    for key in ("BUNDLE", "BUNDLE_SHA256", "BUNDLE_INPUTS", "RELEASE_REVISION", "RELEASE_PLATFORM"):
        monkeypatch.delenv(key, raising=False)
    env_file = work.parent / "normal.env"
    env_file.write_text("EXISTING_CONFIG=kept\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("PROD_ENV_FILE", str(env_file))
    monkeypatch.setenv("RELEASE_COMPOSE_PROJECT", "existing-project")
    monkeypatch.setenv("SCAFFOLD_PROFILE", "example")
    monkeypatch.setenv("EMERGENCY", "1")
    calls: list[tuple[str, ...]] = []
    original_run = bundle.run
    wrong_architecture = False

    def run(*args: str, **kwargs: object) -> bytes | None:
        calls.append(args)
        if args[0] == "git":
            result = original_run(*args, **kwargs)
            assert result is None or isinstance(result, bytes)
            return result
        if args[:2] == ("docker", "info"):
            return b"linux/amd64\n" if wrong_architecture else b"linux/aarch64\n"
        if args[:3] == ("docker", "image", "save"):
            shutil.copyfile(work / "images.tar", args[args.index("--output") + 1])
            return None
        if args[:3] == ("docker", "image", "load") or args[:2] == ("docker", "compose"):
            return b""
        raise AssertionError(f"unexpected network/build command: {args}")

    targets: list[tuple[Path, str]] = []
    fail_checks = False

    def make(
        root: Path, target: str, values: dict[str, str], env: dict[str, str], output: bool = False
    ) -> bytes | None:
        targets.append((root, target))
        assert values["RELEASE_COMPOSE_PROJECT"] == "existing-project"
        assert values["PROD_ENV_FILE"] == str(env_file)
        if target == "checks" and fail_checks:
            raise subprocess.CalledProcessError(1, ["make", "checks"])
        if target == "release-bundle-config":
            manifest = bundle.manifest_values(Path(values["RELEASE_FILE"]))
            return json.dumps(
                {
                    "name": "existing-project",
                    "services": {"app": {"image": manifest["RELEASE_BACKEND_IMAGE"]}},
                }
            ).encode()
        return None

    monkeypatch.setattr(bundle, "run", run)
    monkeypatch.setattr(bundle, "make", make)
    monkeypatch.setattr(
        bundle,
        "inspect",
        lambda *args: {
            "Id": identity,
            "Os": "linux",
            "Architecture": "arm64",
            "RepoDigests": [],
            "Config": {"Labels": {"org.opencontainers.image.revision": revision}},
        },
    )
    bundle.package("hotfix")
    assert all(root == repository for root, _ in targets)
    assert not any("clone" in call or "fetch" in call for call in calls)
    output = repository / bundle.DEFAULT_BUNDLE
    checksum_file = Path(str(output) + ".sha256")
    checksum = checksum_file.read_text()
    assert checksum.strip() == bundle.digest(output)
    assert git("status", "--porcelain", cwd=repository) == b""

    calls.clear()
    checksum_file.write_text("0" * 64)
    with pytest.raises(bundle.Rejected, match="SHA256 mismatch"):
        bundle.destination("install")
    assert not calls
    checksum_file.write_text(checksum)
    wrong_architecture = True
    with pytest.raises(bundle.Rejected, match="architecture differs from destination"):
        bundle.destination("install")
    assert not any(call[:3] == ("docker", "image", "load") for call in calls)
    assert not any(target == "release-bundle-deploy" for _, target in targets)
    wrong_architecture = False
    bundle.destination("install")
    audit = bundle.read_json(repository / ".local/emergency-release/deployment.json")
    assert audit["revision"] == revision
    assert audit["status"] == "succeeded"
    assert audit["project"] == "existing-project"
    assert [target for _, target in targets][-2:] == [
        "release-bundle-deploy",
        "release-bundle-smoke",
    ]
    assert env_file.read_text() == "EXISTING_CONFIG=kept\n"
    assert git("rev-parse", "HEAD", cwd=repository).decode().strip() == revision

    # A subsequent failed attempt must not leave an older bundle selected by defaults.
    previous = output.read_bytes()
    fail_checks = True
    with pytest.raises(subprocess.CalledProcessError):
        bundle.package("hotfix")
    assert output.read_bytes() == previous
    calls.clear()
    with pytest.raises(FileNotFoundError):
        bundle.destination("install")
    assert not calls
