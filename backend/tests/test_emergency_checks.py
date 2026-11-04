import os
import subprocess
from pathlib import Path

import pytest
from test_release_bundle_preflight import bundle

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))


def recorder(tmp_path: Path) -> Path:
    command = tmp_path / "record-command"
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
    command.chmod(0o755)
    return command


@pytest.mark.parametrize(
    "profile, quality",
    [("react-vite", "be.ci fe.ci"), ("server-rendered-django", "build check")],
)
def test_hotfix_hook_does_not_start_or_stop_the_development_stack(
    tmp_path: Path, profile: str, quality: str
) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "release-bundle-check",
            f"SCAFFOLD_PROFILE={profile}",
            f"MAKE={recorder(tmp_path)}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "prepare-release-ci",
        quality,
        "verify-release-images verify-host-ingress verify-monitoring-config",
        "down-release-ci",
    ]


@pytest.mark.parametrize("target, service", [("be.ci", "backend"), ("fe.ci", "frontend")])
def test_quality_containers_do_not_start_dependencies_or_publish_ports(
    tmp_path: Path, target: str, service: str
) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            target,
            "SCAFFOLD_PROFILE=react-vite",
            f"COMPOSE_DEV={recorder(tmp_path)}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    command = result.stdout.strip()
    assert command.startswith("run ")
    assert f"--no-deps {service} " in command
    assert "--rm" in command
    assert "--service-ports" not in command
    assert " up " not in command


@pytest.mark.parametrize("kind", ["ps", "volume", "network"])
def test_validation_refuses_deployment_resources(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("RELEASE_CI_COMPOSE_PROJECT", "dev-scaffold-release")
    calls = []

    def run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        assert args[0] == "docker"
        assert "--filter" in args
        if args[1] == kind and "label=io.dev-scaffold.validation=release-ci" not in args:
            return b"deployment-resource\n"
        return b""

    monkeypatch.setattr(bundle, "run", run)
    with pytest.raises(bundle.Rejected, match="non-CI"):
        bundle.validation_preflight()
    assert not any("down" in call or "up" in call or "pull" in call for call in calls)


@pytest.mark.parametrize("resources", [b"", b"validation-resource\n"])
def test_validation_accepts_only_empty_or_owned_resources(
    monkeypatch: pytest.MonkeyPatch, resources: bytes
) -> None:
    monkeypatch.setenv("RELEASE_CI_COMPOSE_PROJECT", "dev-scaffold-release")
    monkeypatch.setattr(bundle, "run", lambda *args, **kwargs: resources)
    bundle.validation_preflight()


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_failed_preparation_never_registers_cleanup(tmp_path: Path, profile: str) -> None:
    command = recorder(tmp_path)
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\nexit 1\n')
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "release-bundle-check",
            f"SCAFFOLD_PROFILE={profile}",
            f"MAKE={command}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout.splitlines() == ["prepare-release-ci"]


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_cleanup_stops_when_ownership_check_fails(tmp_path: Path, profile: str) -> None:
    python = tmp_path / "python3"
    python.write_text("#!/bin/sh\necho ownership-rejected\nexit 1\n")
    python.chmod(0o755)
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "down-release-ci",
            f"SCAFFOLD_PROFILE={profile}",
            f"COMPOSE_RELEASE_CI={recorder(tmp_path)}",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout.splitlines() == ["ownership-rejected"]


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize(
    "target, suffix", [("up-dev", "dev"), ("down-prod", "prod"), ("deploy-release", "prod")]
)
def test_operator_commands_use_distinct_project_defaults(
    tmp_path: Path, profile: str, target: str, suffix: str
) -> None:
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "project=%s\\n" "$COMPOSE_PROJECT_NAME"\n')
    docker.chmod(0o755)
    manifest = tmp_path / "release.env"
    manifest.touch()
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            target,
            f"SCAFFOLD_PROFILE={profile}",
            "PROJECT_NAME=existing-app",
            f"RELEASE_FILE={manifest}",
            f"PROD_SIM_ENV_FILE={manifest}",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines()
    assert set(result.stdout.splitlines()) == {f"project=existing-app-{suffix}"}


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_release_validation_pulls_database_before_starting_it(profile: str) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-n",
            "verify-release-images",
            f"SCAFFOLD_PROFILE={profile}",
            "PROJECT_NAME=existing-app",
            "RELEASE_PLATFORM=linux/arm64",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.index("validation-preflight") < result.stdout.index("pull db")
    assert result.stdout.index("pull db") < result.stdout.index("up -d --no-build db")
    assert "COMPOSE_PROJECT_NAME=existing-app-release " in result.stdout
    assert "pull backend" not in result.stdout
    assert "pull app" not in result.stdout
