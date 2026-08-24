from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _make(profile: str, *arguments: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", f"SCAFFOLD_PROFILE={profile}", *arguments],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_react_release_path_remains_selected_by_the_react_profile() -> None:
    build = _make("react-vite", "--dry-run", "build-release-images")
    verify = _make("react-vite", "--dry-run", "verify-release-images")
    logs = _make("react-vite", "--dry-run", "logs-release-ci")

    assert "docker build --target prod" in build
    assert "docker build -f deploy/nginx/Dockerfile" in build
    assert "profiles/server-rendered-django" not in build
    assert "deploy/docker-compose.release.yml" in verify
    assert "RELEASE_BACKEND_IMAGE" in verify
    assert "RELEASE_WEB_IMAGE" in verify
    assert "docker-compose.release-ci.yml" in logs
    assert "logs --tail=200" in logs


def test_server_rendered_release_path_builds_and_tests_its_exact_images() -> None:
    build = _make("server-rendered-django", "--dry-run", "build-release-images")
    verify = _make("server-rendered-django", "--dry-run", "verify-release-images")
    logs = _make("server-rendered-django", "--dry-run", "logs-release-ci")

    assert "profiles/server-rendered-django/backend.Dockerfile" in build
    assert "profiles/server-rendered-django/release-nginx.Dockerfile" in build
    assert "deploy/nginx/Dockerfile" not in build
    assert "{{.Config.User}}" in build
    assert '= "app"' in build
    assert '= "nginx"' in build
    assert "profiles/server-rendered-django/release.compose.yml" in verify
    db_start = verify.index("up -d --no-build db")
    production_check = verify.index("python -m app.manage check --deploy")
    migration = verify.index("python -m app.manage migrate")
    static_collection = verify.index("python -m app.manage collectstatic --noinput --clear")
    application_start = verify.index("up -d --no-build app web")
    assert db_start < production_check < migration < static_collection < application_start
    assert verify.count("run --rm --no-deps app") == 4
    assert "up -d --no-build db app" not in verify
    assert "exec -T app python -m app.manage migrate" not in verify
    assert "/static/smoketest.txt" in verify
    assert "/media/release-smoketest.txt" in verify
    assert "stop app" in verify
    assert "profiles/server-rendered-django/release.compose.yml" in logs
    assert "logs --tail=200" in logs


def test_server_rendered_release_preserves_manifest_deploy_and_rollback_contract() -> None:
    makefile = (_repository_root() / "profiles/server-rendered-django/profile.mk").read_text()

    for target in (
        "build-release-images",
        "initialize-release-ci",
        "initialize-release",
        "verify-release-images",
        "push-release-images",
        "deploy-release",
        "rollback-release",
        "down-release-ci",
        "logs-release-ci",
    ):
        assert f"\n{target}:" in makefile
    assert "RELEASE_BACKEND_IMAGE=%s" in makefile
    assert "RELEASE_WEB_IMAGE=%s" in makefile
    assert "docker image inspect" in makefile
    assert "pull app web" in makefile
    assert "deploy-release: initialize-release" in makefile
    assert "rollback-release: deploy-release" in makefile


@pytest.mark.parametrize(
    ("target", "extra_arguments"),
    (
        ("verify-release-images", ("RELEASE_CI_ENV_FILE={ci_env_file}",)),
        ("deploy-release", ("RELEASE_FILE={release_file}",)),
    ),
)
def test_failed_migration_never_starts_a_persistent_application(
    tmp_path: Path,
    target: str,
    extra_arguments: tuple[str, ...],
) -> None:
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *"run --rm --no-deps app python -m app.manage migrate"*) exit 23 ;;\n'
        "esac\n"
    )
    fake_docker.chmod(0o755)
    release_file = tmp_path / "release.env"
    release_file.write_text("SCAFFOLD_PROFILE=server-rendered-django\n")
    ci_env_file = tmp_path / "release-ci.env"
    arguments = tuple(
        argument.format(release_file=release_file, ci_env_file=ci_env_file)
        for argument in extra_arguments
    )
    environment = os.environ.copy()
    environment["DOCKER_LOG"] = str(docker_log)
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "SCAFFOLD_PROFILE=server-rendered-django",
            target,
            *arguments,
        ],
        cwd=_repository_root(),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    commands = docker_log.read_text().splitlines()
    database_start = next(
        index for index, command in enumerate(commands) if "up -d --no-build db" in command
    )
    failed_migration = next(
        index
        for index, command in enumerate(commands)
        if "run --rm --no-deps app python -m app.manage migrate" in command
    )
    assert database_start < failed_migration
    assert not any("up -d --no-build app web" in command for command in commands)
    assert not any("exec -T app" in command for command in commands)
