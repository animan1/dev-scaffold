from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
    assert "up -d --no-build db app" in verify
    assert "python -m app.manage migrate" in verify
    assert "python -m app.manage collectstatic --noinput --clear" in verify
    assert "up -d --no-build web" in verify
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


def test_server_rendered_only_selector_runs_the_shared_release_publication() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()
    server_job = workflow.split("  server-rendered-profile:", 1)[1].split("\n  backend:", 1)[0]
    release_job = workflow.split("  immutable-release:", 1)[1]

    assert "make build-release-images verify-release-images" in server_job
    assert "selected-profile != 'server-rendered-django'" in server_job
    assert "make down down-release-ci" in server_job
    assert "needs: [profile-selection, server-rendered-profile, backend]" in release_job
    assert "always()" in release_job
    assert "outputs.selected-profile" in release_job
    assert "!contains(needs.profile-selection.outputs.ci-profiles, 'react-vite')" in release_job
    assert "needs.backend.result == 'success'" in release_job
    assert (
        "!contains(needs.profile-selection.outputs.ci-profiles, 'server-rendered-django')"
        in release_job
    )
    assert "needs.server-rendered-profile.result == 'success'" in release_job
    assert "run: make build-release-images" in release_job
    assert "run: make verify-release-images" in release_job
    assert release_job.count("uses: anchore/sbom-action@v0") == 2
    assert "RELEASE_BACKEND_SBOM" in release_job
    assert "RELEASE_WEB_SBOM" in release_job
    assert "make push-release-images" in release_job
    assert "RELEASE_BACKEND_IMAGE=//p" in release_job
    assert "RELEASE_WEB_IMAGE=//p" in release_job
