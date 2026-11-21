import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from test_host_ingress import _prompted_prepare

ROOT = next(
    path
    for path in (
        Path(os.environ.get("REPO_DIR", "")),
        Path(__file__).resolve().parents[2],
        Path("/workspace"),
    )
    if (path / "Makefile").is_file()
)
TUNNEL = ROOT / "deploy/remote-db"


@pytest.mark.parametrize(
    "base", ["deploy/docker-compose.dev.yml", "profiles/server-rendered-django/compose.yml"]
)
def test_remote_db_overlay_uses_existing_application_stack(base: str, tmp_path: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI required for Compose configuration validation")
    inputs = {
        "COMPOSE_PROJECT_NAME": "dev-scaffold",
        "HOST_UID": str(os.getuid()),
        "HOST_GID": str(os.getgid()),
        "GIT_COMMON_DIR": str(ROOT / ".git"),
        "REMOTE_DB_BUILD_CONTEXT": str(ROOT),
        "REMOTE_DB_SSH_HOST": "db.example.com",
        "REMOTE_DB_SSH_USER": "operator",
        "REMOTE_DB_SSH_IDENTITY_FILE": str(tmp_path / "identity"),
        "REMOTE_DB_SSH_KNOWN_HOSTS_FILE": str(tmp_path / "known_hosts"),
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / base),
            "-f",
            str(TUNNEL / "compose.yml"),
            "config",
            "--format",
            "json",
        ],
        env={**os.environ, **inputs, "DOCKER_HOST": "unix:///nonexistent"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    tunnel = config["services"]["remote-db-tunnel"]
    assert tunnel["build"]["context"] == str(ROOT)
    assert tunnel["build"]["dockerfile"] == "deploy/remote-db/Dockerfile"
    assert "ports" not in tunnel
    assert tunnel["environment"]["SSH_HOST"] == inputs["REMOTE_DB_SSH_HOST"]
    assert tunnel["environment"]["REMOTE_DB_PORT"] == "15432"
    assert tunnel["read_only"]
    assert tunnel["cap_drop"] == ["ALL"]
    assert "pg_isready" in tunnel["healthcheck"]["test"][1]
    for mount in tunnel["volumes"]:
        assert mount["type"] == "bind" and mount["read_only"]
        assert not mount.get("bind", {}).get("create_host_path", False)
    assert not list(tmp_path.iterdir())  # config must not create missing key paths


def test_remote_listener_is_loopback_only() -> None:
    assert (
        '"127.0.0.1:${REMOTE_DB_LOOPBACK_PORT:-15432}:5432"'
        in (TUNNEL / "listen.compose.yml").read_text()
    )


@pytest.mark.parametrize(
    "setting,value",
    [("SSH_HOST", "-oProxyCommand=bad"), ("REMOTE_DB_PORT", "65536"), ("SSH_USER", "")],
)
def test_tunnel_rejects_invalid_inputs_before_connecting(setting: str, value: str) -> None:
    inputs = {
        "SSH_HOST": "db.example.com",
        "SSH_USER": "operator",
        "SSH_PORT": "22",
        "REMOTE_DB_HOST": "127.0.0.1",
        "REMOTE_DB_PORT": "15432",
        "TUNNEL_PORT": "5432",
    }
    result = subprocess.run(
        ["sh", str(TUNNEL / "tunnel.sh")],
        env={**os.environ, **inputs, setting: value},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert setting in result.stderr


def test_tunnel_keeps_strict_ssh_checks() -> None:
    script = (TUNNEL / "tunnel.sh").read_text()
    for option in (
        "ExitOnForwardFailure=yes",
        "StrictHostKeyChecking=yes",
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "UserKnownHostsFile=",
    ):
        assert option in script


def _project_checkout(tmp_path: Path, enabled: bool, profile: str) -> dict[str, str]:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI required for Compose configuration validation")
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / ".scaffold-profile").write_text(
        f"SCAFFOLD_PROFILE := {profile}\nREMOTE_DB_TUNNEL := {int(enabled)}\n"
        "SCAFFOLD_BACKUP_PROFILE := none\n"
    )
    (tmp_path / "profiles").symlink_to(ROOT / "profiles", target_is_directory=True)
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("docker-compose.prod.yml", "docker-compose.host-ingress.yml", "setup-env.sh"):
        shutil.copyfile(ROOT / "deploy" / name, deploy / name)
    shutil.copytree(TUNNEL, deploy / "remote-db")
    backend_path = tmp_path / "backend"
    backend_path.mkdir()
    settings = (ROOT / "backend/.env.example").read_text()
    settings += "POSTGRES_USER=postgres\nPOSTGRES_PASSWORD=postgres\nPOSTGRES_DB=app\n"
    if enabled:
        settings += (
            "REMOTE_DB_SSH_HOST=db.example.com\nREMOTE_DB_SSH_USER=operator\n"
            f"REMOTE_DB_SSH_IDENTITY_FILE={tmp_path}/identity\n"
            f"REMOTE_DB_SSH_KNOWN_HOSTS_FILE={tmp_path}/known_hosts\n"
            "DATABASE_URL=postgresql://app:example@remote-db-tunnel:5432/app\n"
        )
    (deploy / ".env.prod").write_text(settings)
    (backend_path / ".env").write_text(settings)
    environment = {
        **os.environ,
        "DOCKER_HOST": "unix:///nonexistent",
        "GIT_COMMON_DIR": str(ROOT / ".git"),
        "PROJECT_NAME": "dev-scaffold",
    }
    return environment


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_project_setting_drives_bare_commands(tmp_path: Path, enabled: bool, profile: str) -> None:
    environment = _project_checkout(tmp_path, enabled, profile)
    command = ["make", "--no-print-directory", "-n"]
    targets = ("logs-prod", "down-prod") if profile == "react-vite" else ("logs", "down")
    for target in targets:
        planned = subprocess.run(
            [*command, target],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert ("remote-db/compose.yml" in planned.stdout) is enabled
        assert ".local" not in planned.stdout
        assert "groupadd" not in planned.stdout
    # Resolve exactly the Compose invocation used by the bare logs command.
    words = shlex.split(
        subprocess.check_output(
            [*command, targets[0]],
            cwd=tmp_path,
            env=environment,
            text=True,
        )
    )
    while words[0] != "docker":
        name, value = words.pop(0).split("=", 1)
        environment[name] = value
    result = subprocess.run(
        [*words[: words.index("logs")], "config", "--format", "json"],
        cwd=tmp_path,
        env={**environment, "REMOTE_DB_BUILD_CONTEXT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert "consumer" not in services  # extends must not add another runtime service
    assert ("remote-db-tunnel" in services) is enabled
    app = services["backend" if profile == "react-vite" else "app"]
    if enabled:
        assert app["environment"]["DATABASE_URL"].endswith("@remote-db-tunnel:5432/app")
        assert app["depends_on"]["remote-db-tunnel"]["condition"] == "service_healthy"
        assert "migrate" not in " ".join(app.get("command") or [])
    assert not (tmp_path / "identity").exists()
    assert not (tmp_path / "known_hosts").exists()


@pytest.mark.parametrize("missing", [None, "REMOTE_DB_SSH_HOST", "REMOTE_DB_SSH_IDENTITY_FILE"])
def test_prepare_checks_compose_inputs_without_logging_values(
    tmp_path: Path, missing: str | None
) -> None:
    environment = _project_checkout(tmp_path, True, "react-vite")
    (tmp_path / "identity").write_text("existing identity")
    (tmp_path / "known_hosts").write_text("existing host keys")
    private = tmp_path / "deploy/.env.prod"
    private.write_text(
        private.read_text()
        + (f"{missing}=\n" if missing else "")
        + "DJANGO_SECRET_KEY=do-not-log-this\n"
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "prepare-prod"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if missing:
        assert result.returncode != 0
        assert f"Missing or invalid required production input: {missing}" in output
    else:
        assert result.returncode == 0, output
        assert "Production application/database inputs valid" in output
    assert "do-not-log-this" not in output
    assert "Invalid production configuration" not in output
    assert (tmp_path / "identity").read_text() == "existing identity"
    assert (tmp_path / "known_hosts").read_text() == "existing host keys"


@pytest.mark.parametrize("mode", ["interactive", "abort", "unattended"])
def test_fresh_remote_production_setup(tmp_path: Path, mode: str) -> None:
    environment = _project_checkout(tmp_path, True, "react-vite")
    private = tmp_path / "deploy/.env.prod"
    private.unlink()
    (tmp_path / "identity").write_text("existing identity")
    (tmp_path / "known_hosts").write_text("existing host keys")
    command = ["make", "--no-print-directory", "prepare-prod"]
    secret_url = "postgresql://operator:private-password@remote-db-tunnel:5432/remote"
    if mode == "unattended":
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "REMOTE_DB_SSH_HOST" in result.stderr
        return
    # The shared terminal helper execs the real Make target; no services are started.
    code, output = _prompted_prepare(
        command,
        tmp_path,
        "unused",
        environment=environment,
        extra_responses={
            b"REMOTE_DB_SSH_HOST: ": "\x03" if mode == "abort" else "-invalid\ndb.example.com",
            b"REMOTE_DB_SSH_USER: ": "operator",
            b"REMOTE_DB_SSH_IDENTITY_FILE: ": str(tmp_path / "identity"),
            b"REMOTE_DB_SSH_KNOWN_HOSTS_FILE: ": str(tmp_path / "known_hosts"),
            b"DATABASE_URL: ": secret_url,
        },
    )
    if mode == "abort":
        assert code != 0
        return
    assert code == 0, output
    assert "Production application/database inputs valid" in output
    assert "Invalid REMOTE_DB_SSH_HOST" in output
    assert "private-password" not in output
    assert secret_url in private.read_text()
    assert private.stat().st_mode & 0o777 == 0o600
    original = private.read_bytes()
    rerun = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert private.read_bytes() == original
