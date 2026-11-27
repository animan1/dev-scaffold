from __future__ import annotations

import json
import os
import re
import shlex
import shutil
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
    logs = _make("react-vite", "--dry-run", "logs-ephemeral")
    initialize = _make("react-vite", "--dry-run", "initialize-release")

    assert "docker build --target prod" in build
    assert "docker build -f deploy/nginx/Dockerfile" in build
    assert "profiles/server-rendered-django" not in build
    assert "deploy/docker-compose.release.yml" in verify
    assert "--env-file deploy/releases/" in verify
    assert "docker-compose.ephemeral.yml" in logs
    assert "logs -f --tail=200" in logs
    assert "pull backend nginx" in initialize
    assert "run --rm --no-deps backend python -m app.manage migrate" in initialize
    assert "collectstatic --noinput --clear" in initialize


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_environment_namespaces_do_not_inherit_global_compose_project(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "tenant-prod")
    for suffix, targets in (
        ("dev", ("up-dev", "down-dev", "reset-dev", "logs-dev", "ps-dev")),
        ("prod", ("up-prod", "down-prod", "deploy-release", "rollback-release", "prepare-prod")),
        ("ephemeral", ("verify-release-images", "logs-ephemeral", "down-ephemeral")),
    ):
        output = _make(profile, "PROJECT_NAME=tenant", "--dry-run", *targets)
        projects = re.findall(r"COMPOSE_PROJECT_NAME=([^\s]+)", output)
        assert projects
        assert set(projects) == {f"tenant-{suffix}"}
    verification = _make(profile, "--dry-run", "verify-ephemeral")
    if profile == "react-vite":
        assert "docker compose" in verification and " logs || true" in verification
    else:
        assert "docker " not in verification
    assert "migrate" not in verification
    stopped = _make(profile, "--dry-run", "down-ephemeral")
    removed = _make(profile, "--dry-run", "rm-ephemeral")
    assert "down -v" not in stopped
    assert "down -v" in removed


@pytest.mark.skipif(shutil.which("docker") is None, reason="Needs Docker Compose client")
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize("host_ingress", ["0", "1"])
def test_release_configuration_resolves_production_project_and_volumes(
    profile: str, host_ingress: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent")
    private = tmp_path / "production.env"
    private.write_text(
        "POSTGRES_USER=app\nPOSTGRES_PASSWORD=test-only\nPOSTGRES_DB=app\n"
        f"EPHEMERAL_CERTS_PATH={tmp_path}/certs\n"
    )
    manifest = tmp_path / "release.env"
    manifest.write_text(
        "RELEASE_BACKEND_IMAGE=local/backend:test\nRELEASE_WEB_IMAGE=local/web:test\n"
        "RELEASE_REVISION=test\nRELEASE_IMAGE_PREFIX=local/app\n"
    )
    for project in ("tenant-prod", "legacy-tenant"):
        config = json.loads(
            _make(
                profile,
                "-s",
                "release-bundle-config",
                "PROJECT_NAME=tenant",
                f"PROD_COMPOSE_PROJECT={project}",
                "SCAFFOLD_BACKUP_PROFILE=none",
                f"HOST_INGRESS={host_ingress}",
                "HOST_INGRESS_PORT=19080",
                "RELEASE_HTTP_PORT=28080",
                "PROD_HTTPS_PORT=28043",
                f"PROD_ENV_FILE={os.path.relpath(private, _repository_root())}",
                f"RELEASE_FILE={manifest}",
            )
        )
        assert config["name"] == project
        application = "app" if profile == "server-rendered-django" else "backend"
        assert config["services"][application]["image"] == "local/backend:test"
        assert config["services"]["db"]["environment"]["POSTGRES_PASSWORD"] == "test-only"
        web = "web" if profile == "server-rendered-django" else "nginx"
        if profile == "server-rendered-django" and host_ingress == "0":
            assert not config["services"][web].get("ports")
            ingress = config["services"]["ingress"]
            ports = [
                (port["host_ip"], port["published"], port["target"]) for port in ingress["ports"]
            ]
            assert ports == [
                ("127.0.0.1", "19080", 80),
                ("127.0.0.1", "28043", 443),
            ]
            assert ingress["environment"]["INGRESS_HTTPS_PORT"] == "28043"
            assert all(mount["read_only"] for mount in ingress["volumes"])
        else:
            assert "ingress" not in config["services"]
            assert config["services"][web]["ports"][0]["published"] == "19080"
        if profile == "react-vite" and host_ingress == "0":
            nginx = config["services"]["nginx"]
            assert nginx["ports"][1]["published"] == "28043"
            assert nginx["environment"]["PROD_HTTPS_PORT"] == "28043"
            assert (
                nginx["environment"]["NGINX_ENVSUBST_FILTER"].replace("$$", "$")
                == "^PROD_HTTPS_PORT$"
            )
        assert all(
            volume["name"].startswith(f"{project}_") for volume in config["volumes"].values()
        )
        if profile == "server-rendered-django":
            for mount in config["services"]["web"]["volumes"]:
                assert mount["read_only"] is True
                assert mount["volume"]["nocopy"] is True
            assert not any(
                mount.get("volume", {}).get("nocopy", False)
                for mount in config["services"]["app"]["volumes"]
            )
    if profile == "react-vite":
        ephemeral = json.loads(
            _make(
                profile,
                "-s",
                "release-bundle-config",
                "COMPOSE_PROD=$(COMPOSE_EPHEMERAL)",
                "SCAFFOLD_BACKUP_PROFILE=none",
                f"EPHEMERAL_ENV_FILE={private}",
                f"RELEASE_FILE={manifest}",
                "PROD_HTTPS_PORT=28043",
                "EPHEMERAL_HTTPS_PORT=28044",
            )
        )["services"]["nginx"]
        assert ephemeral["environment"]["PROD_HTTPS_PORT"] == "28044"
        assert ephemeral["ports"][1]["published"] == "28044"
    else:
        ephemeral = json.loads(
            _make(
                profile,
                "-s",
                "release-bundle-config",
                "COMPOSE_PROD=$(COMPOSE_EPHEMERAL)",
                "SCAFFOLD_BACKUP_PROFILE=none",
                f"EPHEMERAL_ENV_FILE={private}",
                f"RELEASE_FILE={manifest}",
                "EPHEMERAL_HTTP_PORT=28081",
                "EPHEMERAL_HTTPS_PORT=28044",
                "HOST_INGRESS=0",
            )
        )
        assert not ephemeral["services"]["web"].get("ports")
        ingress = ephemeral["services"]["ingress"]
        assert [port["published"] for port in ingress["ports"]] == ["28081", "28044"]
        assert any(
            mount["source"] == str(tmp_path / "certs") and mount["read_only"]
            for mount in ingress["volumes"]
        )


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_shared_lifecycle_commands_select_each_requested_environment(profile: str) -> None:
    default_dev = _make(profile, "--dry-run", "smoke")
    assert "http://127.0.0.1:8080/api/healthz" in default_dev
    assert "http://localhost" not in default_dev
    if profile == "server-rendered-django":
        assert "Host: 127.0.0.1" in default_dev
        assert "Host: localhost" not in default_dev
    dev = _make(profile, "--dry-run", "smoke", "DEV_URL_ROOT=http://dev.example")
    assert "http://dev.example/api/healthz" in dev
    assert "http://dev.example/static/smoketest.txt" in dev
    custom_port = _make(
        profile, "--dry-run", "smoke", "DEV_HTTP_PORT=18123", "APP_HOST=dev.example"
    )
    assert "http://127.0.0.1:18123/api/healthz" in custom_port
    assert "Host: dev.example" in custom_port
    for command in ("down", "logs", "ps"):
        output = _make(
            profile,
            "--dry-run",
            "PROJECT_NAME=tenant",
            *(f"{command}-{env}" for env in ("dev", "prod", "ephemeral")),
        )
        assert re.findall(r"COMPOSE_PROJECT_NAME=([^\s]+)", output) == [
            "tenant-dev",
            "tenant-prod",
            "tenant-ephemeral",
        ]
    waiting = _make(
        profile,
        "--dry-run",
        "PROJECT_NAME=tenant",
        "wait-prod",
        "DEV_URL_ROOT=http://dev.example",
        "PROD_HTTPS_URL=https://tenant.example:9443",
    )
    assert "COMPOSE_PROJECT_NAME=tenant-prod" in waiting
    assert "tenant-dev" not in waiting
    assert "X-Forwarded-Proto" not in waiting
    assert "https://tenant.example:9443/api/healthz" in waiting
    assert "http://dev.example" not in waiting
    smoke = _make(
        profile,
        "--dry-run",
        "smoke-prod",
        "HOST_INGRESS_PORT=19080",
        "RELEASE_HTTP_PORT=28080",
        "APP_HOST=dev.example",
        "HOST_INGRESS_HOST=tenant.example",
    )
    assert "https://tenant.example/api/healthz" in smoke
    assert "Waiting for backend on https://tenant.example/api/healthz" in smoke
    assert "--proto '=https' --proto-redir '=https' -L" in smoke
    assert smoke.count("/static/smoketest.txt") == 1
    assert smoke.count("grep -qi") == 1
    direct = _make(
        profile,
        "--dry-run",
        "smoke-prod",
        "HOST_INGRESS=0",
        "HOST_INGRESS_PORT=19080",
        "HOST_INGRESS_HOST=tenant.example",
        "CURL_FLAGS=-k",
    )
    assert "https://127.0.0.1:18443/api/healthz" in direct
    assert "--cacert 'deploy/nginx/certs/server.crt'" in direct
    assert "X-Forwarded-Proto" not in direct
    assert "curl -k" not in direct
    assert "X-Forwarded-Proto" not in smoke
    if profile == "react-vite":
        origin = _make(profile, "--dry-run", "verify-host-ingress")
        assert "Django admin slash redirect was not routed" in origin
        assert "X-Forwarded-Proto: https" in origin


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_restart_aborts_before_startup_when_teardown_fails(profile: str) -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            f"SCAFFOLD_PROFILE={profile}",
            "COMPOSE_DEV=false",
            "restart-dev",
        ],
        cwd=_repository_root(),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "false down --remove-orphans" in result.stdout
    assert "up-dev" not in result.stdout


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
@pytest.mark.parametrize("target", ["smoke", "smoke-prod"])
@pytest.mark.parametrize("missing_static", [False, True])
def test_smoke_requires_static_delivery_in_both_profiles(
    profile: str, target: str, missing_static: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curl = tmp_path / "curl"
    log = tmp_path / "requests"
    scheme = "https" if target == "smoke-prod" else "http"
    url_root = f"{scheme}://smoke.test"
    url_variable = "PROD_HTTPS_URL" if target == "smoke-prod" else "DEV_URL_ROOT"
    curl.write_text(
        "#!/bin/sh\n"
        "for url; do :; done\n"
        'printf "%s\\n" "$url" >> "$REQUEST_LOG"\n'
        'case "$url" in */static/smoketest.txt)\n'
        '  if [ "$MISSING_STATIC" = 1 ]; then exit 22; fi ;;\n'
        "esac\n"
        'printf "%s\\n" "$HOME_BODY"\n'
    )
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("REQUEST_LOG", str(log))
    monkeypatch.setenv("MISSING_STATIC", str(int(missing_static)))
    monkeypatch.setenv(
        "HOME_BODY",
        "<main>Django</main>" if profile == "server-rendered-django" else '<div id="root"></div>',
    )
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            f"SCAFFOLD_PROFILE={profile}",
            "HOST_INGRESS=0",
            f"{url_variable}={url_root}",
            target,
        ],
        cwd=_repository_root(),
        capture_output=True,
        text=True,
    )
    requests = log.read_text().splitlines()
    assert requests[:3] == [
        f"{url_root}/api/healthz",
        f"{url_root}/api/healthz",
        f"{url_root}/static/smoketest.txt",
    ]
    assert (result.returncode != 0) == missing_static
    assert (f"{url_root}/" in requests) != missing_static


def test_server_rendered_release_path_builds_and_tests_its_exact_images() -> None:
    build = _make("server-rendered-django", "--dry-run", "build-release-images")
    verify = _make("server-rendered-django", "--dry-run", "verify-release-images", "HOST_INGRESS=0")
    logs = _make("server-rendered-django", "--dry-run", "logs-ephemeral")

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
    application_start = verify.index("up -d --no-build\n")
    assert db_start < production_check < migration < static_collection < application_start
    assert verify.count("run --rm --no-deps app") == 4
    assert "up -d --no-build db app" not in verify
    assert "exec -T app python -m app.manage migrate" not in verify
    before_stop, after_stop = verify.split("stop app", 1)
    for path in ("static/smoketest.txt", "media/release-smoketest.txt"):
        url = f"https://127.0.0.1:18444/{path}"
        assert url in before_stop
        assert url in after_stop
    assert "profiles/server-rendered-django/release.compose.yml" in logs
    assert "logs -f --tail=200" in logs


def test_server_rendered_release_preserves_manifest_deploy_and_rollback_contract() -> None:
    makefile = (_repository_root() / "profiles/server-rendered-django/profile.mk").read_text()
    makefile += (_repository_root() / "Makefile").read_text()

    for target in (
        "build-release-images",
        "initialize-ephemeral",
        "initialize-release",
        "verify-release-images",
        "push-release-images",
        "deploy-release",
        "rollback-release",
        "down-ephemeral",
        "logs-ephemeral",
    ):
        assert f"\n{target}:" in makefile
    assert "RELEASE_BACKEND_IMAGE=%s" in makefile
    assert "RELEASE_WEB_IMAGE=%s" in makefile
    assert "docker image inspect" in makefile
    assert "deploy-release: initialize-release" in makefile
    assert "rollback-release: deploy-release" in makefile


@pytest.mark.parametrize(
    ("target", "extra_arguments"),
    (
        (
            "verify-release-images",
            ("EPHEMERAL_ENV_FILE={ci_env_file}", "RELEASE_FILE={release_file}"),
        ),
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
    real_docker = shutil.which("docker")
    config_check = (
        f'  *" config --quiet") {shlex.quote(real_docker)} "$@" ;;\n' if real_docker else ""
    )
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "${COMPOSE_PROJECT_NAME:-build}" "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        + config_check
        + '  *"run --rm --no-deps app python -m app.manage migrate"*) exit 23 ;;\n'
        "esac\n"
    )
    fake_docker.chmod(0o755)
    release_file = tmp_path / "release.env"
    release_file.write_text(
        "SCAFFOLD_PROFILE=server-rendered-django\nRELEASE_BACKEND_IMAGE=local/backend:test\n"
        "RELEASE_WEB_IMAGE=local/web:test\nRELEASE_REVISION=test\nRELEASE_IMAGE_PREFIX=local/app\n"
    )
    prod_file = tmp_path / "production.env"
    prod_file.write_text("POSTGRES_USER=app\nPOSTGRES_PASSWORD=test-only\nPOSTGRES_DB=app\n")
    ci_env_file = tmp_path / "release-ci.env"
    arguments = tuple(
        argument.format(release_file=release_file, ci_env_file=ci_env_file)
        for argument in extra_arguments
    )
    environment = os.environ.copy()
    environment["DOCKER_LOG"] = str(docker_log)
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    environment["DOCKER_HOST"] = "unix:///nonexistent"

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "SCAFFOLD_PROFILE=server-rendered-django",
            "SCAFFOLD_BACKUP_PROFILE=none",
            f"PROD_ENV_FILE={prod_file}",
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
    suffix = "ephemeral" if target == "verify-release-images" else "prod"
    assert all(
        command.startswith(f"{_repository_root().name}-{suffix} ")
        for command in commands
        if " compose " in command
    )
    database_start = next(
        index for index, command in enumerate(commands) if "up -d --no-build db" in command
    )
    failed_migration = next(
        index
        for index, command in enumerate(commands)
        if "run --rm --no-deps app python -m app.manage migrate" in command
    )
    assert database_start < failed_migration
    assert not any(command.endswith("up -d --no-build") for command in commands)
    assert not any("exec -T app" in command for command in commands)


def test_server_rendered_only_selector_runs_the_shared_release_publication() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()
    server_job = workflow.split("  server-rendered-profile:", 1)[1].split("\n  backend:", 1)[0]
    gate_job = workflow.split("  immutable-release-gate:", 1)[1].split("\n  immutable-release:", 1)[
        0
    ]
    release_job = workflow.split("  immutable-release:", 1)[1]

    assert "make ci-ephemeral" in server_job
    assert "needs: [profile-selection, server-rendered-profile, backend]" in gate_job
    assert "scripts/check-immutable-release-gate" in gate_job
    assert "--react-result '${{ needs.backend.result }}'" in gate_job
    assert "--server-rendered-result '${{ needs.server-rendered-profile.result }}'" in gate_job
    assert "needs: [profile-selection, immutable-release-gate]" in release_job
    assert "always()" in release_job
    assert "needs.immutable-release-gate.result == 'success'" in release_job
    assert "run: make build-release-images" in release_job
    assert "run: make verify-release-images" in release_job
    assert release_job.count("uses: anchore/sbom-action@v0") == 2
    assert "RELEASE_BACKEND_SBOM" in release_job
    assert "RELEASE_WEB_SBOM" in release_job
    assert "make push-release-images" in release_job
    assert "RELEASE_BACKEND_IMAGE=//p" in release_job
    assert "RELEASE_WEB_IMAGE=//p" in release_job
