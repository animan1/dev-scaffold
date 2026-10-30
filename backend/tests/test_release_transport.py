from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))


def dry_run(profile: str, target: str, *options: str) -> str:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--dry-run",
            f"SCAFFOLD_PROFILE={profile}",
            target,
            *options,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_release_build_targets_platform_and_labels_exact_revision(profile: str) -> None:
    revision = "a" * 40
    output = dry_run(
        profile,
        "build-release-images",
        "RELEASE_PLATFORM=linux/arm64",
        f"RELEASE_REVISION={revision}",
    )
    assert output.count("--platform linux/arm64") == 2
    assert output.count(f"--label org.opencontainers.image.revision={revision}") == 2


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_normal_release_keeps_native_build_and_pull_behavior(profile: str) -> None:
    assert "--platform" not in dry_run(profile, "build-release-images", "RELEASE_PLATFORM=")
    output = dry_run(profile, "deploy-release")
    assert "pull " in output
    assert output.index("pull ") < output.index("up -d")


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_installer_can_reuse_migrations_with_an_explicit_no_pull_command(profile: str) -> None:
    output = dry_run(
        profile, "deploy-release", "RELEASE_PULL=true", "COMPOSE_RELEASE=validated-compose"
    )
    assert "pull " not in output
    assert "validated-compose" in output
    assert "python -m app.manage migrate" in output
    assert "python -m app.manage collectstatic" in output
    assert "--no-build" in output


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_runtime_inventory_includes_optional_services(profile: str) -> None:
    output = dry_run(profile, "release-bundle-config", "PROD_ENV_FILE=/tmp/private-production.env")
    assert "--profile '*' config --format json" in output
    assert "PROD_ENV_FILE=/tmp/private-production.env" in output
