import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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


def test_persistent_settings_drive_bare_production_commands(tmp_path: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI required for Compose configuration validation")
    local = tmp_path / ".local"
    local.mkdir()
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("docker-compose.prod.yml", "docker-compose.host-ingress.yml"):
        shutil.copyfile(ROOT / "deploy" / name, deploy / name)
    (local / "settings.mk").write_text(
        f"export REMOTE_DB_BUILD_CONTEXT := {ROOT}\n"
        "SCAFFOLD_BACKUP_PROFILE := none\n"
        f"PROD_COMPOSE_FILES += -f {TUNNEL}/compose.yml "
        "-f .local/remote-db-app.compose.yml\n"
    )
    (local / "remote-db-app.compose.yml").write_text(
        "services:\n  backend:\n    depends_on:\n"
        "      remote-db-tunnel:\n        condition: service_healthy\n"
    )
    (deploy / ".env.prod").write_text(
        "REMOTE_DB_SSH_HOST=db.example.com\nREMOTE_DB_SSH_USER=operator\n"
        f"REMOTE_DB_SSH_IDENTITY_FILE={local}/identity\n"
        f"REMOTE_DB_SSH_KNOWN_HOSTS_FILE={local}/known_hosts\n"
        "DATABASE_URL=postgresql://app:example@remote-db-tunnel:5432/app\n"
    )
    environment = {**os.environ, "DOCKER_HOST": "unix:///nonexistent"}
    command = ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"), "-n"]
    for target in ("logs-prod", "down-prod"):
        planned = subprocess.run(
            [*command, target],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(TUNNEL / "compose.yml") in planned.stdout
        assert ".local/remote-db-app.compose.yml" in planned.stdout
        assert "groupadd" not in planned.stdout
    # Resolve exactly the Compose invocation used by the bare logs command.
    words = shlex.split(
        subprocess.check_output(
            [*command, "logs-prod"],
            cwd=tmp_path,
            env=environment,
            text=True,
        )
    )
    name, value = words.pop(0).split("=", 1)
    result = subprocess.run(
        [*words[: words.index("logs")], "config", "--format", "json"],
        cwd=tmp_path,
        env={**environment, name: value, "REMOTE_DB_BUILD_CONTEXT": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    backend = json.loads(result.stdout)["services"]["backend"]
    assert backend["environment"]["DATABASE_URL"].endswith("@remote-db-tunnel:5432/app")
    assert backend["depends_on"]["remote-db-tunnel"]["condition"] == "service_healthy"
    assert not (local / "identity").exists()
    assert not (local / "known_hosts").exists()
