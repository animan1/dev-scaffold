from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(*arguments: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", *arguments],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_backup_image_uses_immutable_versioned_component_images() -> None:
    dockerfile = (_repository_root() / "profiles/immutable-backup/Dockerfile").read_text()

    for expected in (
        "postgres:16.10-bookworm@sha256:",
        "restic/restic:0.19.1@sha256:",
        "rclone/rclone:1.74.4@sha256:",
    ):
        assert expected in dockerfile


def test_backup_image_is_application_independent_and_non_root() -> None:
    dockerfile = (_repository_root() / "profiles/immutable-backup/Dockerfile").read_text()

    assert "USER scaffold-backup" in dockerfile
    assert "COPY backend" not in dockerfile
    assert "COPY profiles" not in dockerfile
    assert "DJANGO" not in dockerfile
    assert "cuplr" not in dockerfile.lower()


def test_backup_image_make_contract_uses_declared_inputs() -> None:
    build = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "build-backup-image")
    verify = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "verify-backup-image")

    assert "profiles/immutable-backup/Dockerfile" in build
    assert "--build-arg POSTGRES_IMAGE=postgres:16.10-bookworm@sha256:" in build
    assert "--build-arg RESTIC_IMAGE=restic/restic:0.19.1@sha256:" in build
    assert "--build-arg RCLONE_IMAGE=rclone/rclone:1.74.4@sha256:" in build
    assert "docker image inspect" in verify
    assert "docker run --rm --entrypoint sh" in verify
    assert "pg_dump --version" in verify
    assert "restic version" in verify
    assert "rclone version" in verify


def test_backup_component_update_workflow_is_make_driven() -> None:
    pull = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "backup-images-pull")
    digests = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "backup-images-digests")

    assert "docker pull postgres:16.10-bookworm" in pull
    assert "docker pull restic/restic:0.19.1" in pull
    assert "docker pull rclone/rclone:1.74.4" in pull
    assert "docker image inspect" in digests


def test_backup_image_version_report_is_make_driven() -> None:
    report = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "backup-image-versions")

    assert "pg_dump --version" in report
    assert "restic version" in report
    assert "rclone version" in report


def test_aggregate_verification_includes_backup_image() -> None:
    default_verify = _make("SCAFFOLD_BACKUP_PROFILE=none", "verify")
    selected_verify = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "verify")

    assert "profiles/immutable-backup/Dockerfile" not in default_verify
    assert "profiles/immutable-backup/Dockerfile" in selected_verify


def test_backup_profile_is_selected_and_verified_independently() -> None:
    repository = _repository_root()
    selector = (repository / ".scaffold-profile").read_text()
    makefile = (repository / "Makefile").read_text()
    workflow = (repository / ".github/workflows/ci.yml").read_text()

    assert "SCAFFOLD_BACKUP_PROFILE ?= none" in selector
    assert "CI_BACKUP_PROFILES ?= immutable-backup" in selector
    assert "profiles/$(SCAFFOLD_BACKUP_PROFILE)/profile.mk" in makefile
    assert "immutable-backup-profile:" in workflow
    assert "SCAFFOLD_BACKUP_PROFILE=immutable-backup verify-backup-image" in workflow
