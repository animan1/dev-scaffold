from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
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
        "build",
        "RELEASE_PLATFORM=linux/arm64",
        f"RELEASE_REVISION={revision}",
    )
    build_commands = output.split("python3 scripts/record_local_release.py", 1)[0]
    assert build_commands.count("--platform linux/arm64") == 2
    assert f"test \"$head\" = '{revision}'" in output
    assert output.count('--label org.opencontainers.image.revision="$head"') == 2


@pytest.mark.parametrize("change", ["clean", "tracked", "staged", "untracked", "wrong-revision"])
def test_build_checks_revision_and_uses_committed_context(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    files = (
        "Makefile",
        ".scaffold-profile",
        ".gitignore",
        "backend/Dockerfile",
        "profiles/release-transport.mk",
    )
    for name in files:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "--", *files], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=checkout,
        check=True,
    )
    (checkout / "backend/.env.private").write_text("ignored\n")
    if change in {"tracked", "staged"}:
        (checkout / "backend/Dockerfile").write_text("modified\n")
    if change == "staged":
        subprocess.run(["git", "add", "--", "backend/Dockerfile"], cwd=checkout, check=True)
    if change == "untracked":
        (checkout / "backend/untracked.txt").write_text("untracked\n")

    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "for context; do :; done\n"
        'test -f "$context/Dockerfile" && test ! -e "$context/.env.private" || exit 1\n'
        'touch "$DOCKER_LOG"\n'
        "exit 23\n"
    )
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    command = ["make", "build", "SCAFFOLD_PROFILE=react-vite", "SCAFFOLD_BACKUP_PROFILE=none"]
    if change == "wrong-revision":
        command.append(f"RELEASE_REVISION={'0' * 40}")
    result = subprocess.run(command, cwd=checkout, capture_output=True, text=True)
    assert result.returncode != 0
    assert log.exists() == (change == "clean"), result.stderr
    if change == "clean":
        assert "Error 23" in result.stderr
    elif change == "wrong-revision":
        assert "RELEASE_REVISION must match HEAD" in result.stderr
    else:
        assert "Commit changes before building" in result.stderr


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_normal_release_keeps_native_build_and_pull_behavior(profile: str) -> None:
    assert "--platform" not in dry_run(profile, "build", "RELEASE_PLATFORM=")
    output = dry_run(profile, "deploy-release")
    assert "pull " in output
    assert output.index("pull ") < output.index("up -d")


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_installer_can_reuse_migrations_with_an_explicit_no_pull_command(profile: str) -> None:
    output = dry_run(
        profile, "deploy-release", "RELEASE_PULL=true", "COMPOSE_PROD=validated-compose"
    )
    assert "pull " not in output
    assert "validated-compose" in output
    assert "python -m app.manage migrate" in output
    assert "python -m app.manage collectstatic" in output
    assert output.count(" up -d --no-build\n") == 1
    assert "--force-recreate" not in output


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_runtime_inventory_includes_optional_services(profile: str) -> None:
    output = dry_run(profile, "release-bundle-config", "PROD_ENV_FILE=/tmp/private-production.env")
    assert "--profile '*' config --format json" in output
    assert "PROD_ENV_FILE=/tmp/private-production.env" in output


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize("missing_digest", [False, True])
def test_push_records_both_digests_and_selected_profile(
    profile: str, missing_digest: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  push) printf "%s\\n" "$2" >> "$PUSH_LOG" ;;\n'
        '  image) case "$4" in\n'
        '    *-web:*) [ "$MISSING_DIGEST" = 0 ] || exit 0 ;;\n'
        '  esac; printf "%s@sha256:%s\\n" "$(echo "$4" | cut -d: -f1)" "$DIGEST" ;;\n'
        "  *) exit 1 ;;\nesac\n"
    )
    docker.chmod(0o755)
    log = tmp_path / "pushes"
    manifest = tmp_path / "release.env"
    manifest.write_text("previous manifest\n")
    digest = "b" * 64
    revision = "a" * 40
    prefix = "registry.example/tenant"
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("PUSH_LOG", str(log))
    monkeypatch.setenv("MISSING_DIGEST", str(int(missing_digest)))
    monkeypatch.setenv("DIGEST", digest)

    result = subprocess.run(
        [
            "make",
            "push-release-images",
            f"SCAFFOLD_PROFILE={profile}",
            f"RELEASE_REVISION={revision}",
            f"RELEASE_IMAGE_PREFIX={prefix}",
            f"RELEASE_FILE={manifest}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert log.read_text().splitlines() == [
        f"{prefix}-backend:{revision}",
        f"{prefix}-web:{revision}",
    ]
    assert (result.returncode != 0) is missing_digest
    expected = (
        "previous manifest\n"
        if missing_digest
        else (
            f"SCAFFOLD_PROFILE={profile}\n"
            f"RELEASE_REVISION={revision}\n"
            f"RELEASE_IMAGE_PREFIX={prefix}\n"
            f"RELEASE_BACKEND_IMAGE={prefix}-backend@sha256:{digest}\n"
            f"RELEASE_WEB_IMAGE={prefix}-web@sha256:{digest}\n"
        )
    )
    assert manifest.read_text() == expected


@pytest.mark.parametrize("failure", [None, "revision", "platform", "user"])
def test_local_manifest_pins_verified_images_or_preserves_previous_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    spec = importlib.util.spec_from_file_location(
        "record_local_release", ROOT / "scripts/record_local_release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    revision = "a" * 40
    labels = {"org.opencontainers.image.revision": revision}
    image = {
        "Id": "sha256:" + "b" * 64,
        "Os": "linux",
        "Architecture": "arm64",
        "Config": {"Labels": labels, "User": "root" if failure == "user" else "app"},
    }
    if failure == "revision":
        labels["org.opencontainers.image.revision"] = "c" * 40
    if failure == "platform":
        image["Architecture"] = "amd64"
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *args, **kwargs: json.dumps([image])
    )
    output = tmp_path / "release.env"
    output.write_text("previous release\n")
    options = argparse.Namespace(
        revision=revision,
        profile="react-vite",
        prefix="local/dev-scaffold",
        backend="backend:commit",
        web="web:commit",
        backend_user="app",
        web_user=None,
        platform="linux/arm64",
        output=str(output),
    )
    if failure:
        with pytest.raises(ValueError):
            module.record(options)
        assert output.read_text() == "previous release\n"
    else:
        module.record(options)
        assert f"RELEASE_BACKEND_IMAGE={image['Id']}" in output.read_text()
        assert f"RELEASE_WEB_IMAGE={image['Id']}" in output.read_text()
        assert output.stat().st_mode & 0o077 == 0
