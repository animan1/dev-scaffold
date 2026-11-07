from __future__ import annotations

import grp
import os
import runpy
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module() -> SimpleNamespace:
    root = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
    return SimpleNamespace(**runpy.run_path(str(root / "scripts/validate_prod_config.py")))


@pytest.mark.parametrize("project,gid", [("cuplr", 12001), ("cranescastle-frappe", 12002)])
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_backup_groups_are_namespaced_and_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project: str,
    gid: int,
    platform: str,
) -> None:
    module = _module()
    private_env = tmp_path / "private.env"
    private_env.write_text("preserve=original\n")
    monkeypatch.setenv("PROD_ENV_FILE", str(private_env))
    monkeypatch.delenv("BACKUP_RUNTIME_GID", raising=False)
    monkeypatch.setattr(module.sys, "platform", platform)
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(module.os, "getgid", lambda: 1000)
    monkeypatch.setattr(module.os, "getgroups", lambda: [1000])
    monkeypatch.setattr(
        module.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="operator", pw_gid=1000)
    )
    name = f"{project}-backup"
    groups: dict[str, grp.struct_group] = {}
    commands: list[list[str]] = []

    def lookup(group: str) -> grp.struct_group:
        return groups[group]

    def run(command: list[str], privileged: bool = False) -> None:
        assert privileged
        assert name in command
        commands.append(command)
        groups[name] = grp.struct_group((name, "x", gid, ["operator"] if len(commands) > 1 else []))

    monkeypatch.setattr(module.grp, "getgrnam", lookup)
    monkeypatch.setitem(module.prepare_backup_group.__globals__, "run_host_command", run)
    module.prepare_backup_group({"COMPOSE_PROJECT_NAME": project})
    assert len(commands) == 2
    assert commands[0][0] == ("dseditgroup" if platform == "darwin" else "groupadd")
    assert "operator" in commands[1]
    assert f'BACKUP_GROUP="{name}"' in private_env.read_text()
    assert f'BACKUP_RUNTIME_GID="{gid}"' in private_env.read_text()
    original = private_env.read_bytes()
    module.prepare_backup_group(
        {"COMPOSE_PROJECT_NAME": project, "BACKUP_GROUP": name, "BACKUP_RUNTIME_GID": str(gid)}
    )
    assert len(commands) == 2
    assert private_env.read_bytes() == original


def _configuration(directory: Path, sharing: str) -> dict[str, object]:
    return {
        "services": {
            "backup": {
                "user": "10001:10001",
                "volumes": [
                    {"type": "bind", "source": str(directory / "repository"), "target": "/backups"},
                    {
                        "type": "bind",
                        "source": str(directory / "rclone"),
                        "target": "/config/rclone",
                    },
                ],
            },
            "monitor": {
                "user": "10002:10002"
                if sharing in ("supplementary", "mismatch")
                else "10002:10001",
                "group_add": ["10001"] if sharing == "supplementary" else [],
                "volumes": []
                if not sharing
                else [
                    {
                        "type": "bind",
                        "source": str(directory / "repository"),
                        "target": "/inspect",
                        "read_only": sharing != "write",
                    },
                ],
            },
        }
    }


@pytest.mark.skipif(os.geteuid() != 0, reason="Fixed service UID ownership runs in make be.ci")
@pytest.mark.parametrize("sharing", ["", "read", "write", "supplementary"])
def test_prepared_paths_preserve_host_owner_and_share_with_fixed_service_uid(
    tmp_path: Path,
    sharing: str,
) -> None:
    prepare = _module().prepare_backup_paths
    config = _configuration(tmp_path, sharing)
    credential = tmp_path / "rclone/rclone.conf"
    credential.parent.mkdir()
    credential.write_text("existing-provider-credential")
    credential_info = credential.stat()
    directory_info = credential.parent.stat()
    repository = tmp_path / "repository"
    repository.mkdir()
    os.chown(repository, 501, 10001)
    prepare(config)
    info = repository.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (501, 10001, 0o2770)
    subprocess.run(
        ["sh", "-c", "printf worker-data > worker-file"],
        cwd=repository,
        user=10001,
        group=10001,
        check=True,
    )
    subprocess.run(
        ["sh", "-c", "test -r worker-file && touch operator-file"],
        cwd=repository,
        user=501,
        group=10001,
        check=True,
    )
    subprocess.run(
        ["sh", "-c", "test ! -r worker-file"],
        cwd=repository,
        user=10001,
        group=10002,
        extra_groups=[],
        check=True,
    )
    assert (credential.parent.stat().st_uid, credential.parent.stat().st_gid) == (
        directory_info.st_uid,
        directory_info.st_gid,
    )
    assert credential.parent.stat().st_mode == directory_info.st_mode
    assert credential.read_text() == "existing-provider-credential"
    assert (credential.stat().st_uid, credential.stat().st_gid) == (
        credential_info.st_uid,
        credential_info.st_gid,
    )
    assert credential.stat().st_mode == credential_info.st_mode
    sentinel = repository / "existing-data"
    sentinel.write_text("preserve")
    ownership = (sentinel.stat().st_uid, sentinel.stat().st_gid)
    repository.chmod(0o755)
    prepare(config)
    assert repository.stat().st_mode & 0o7777 == 0o2770
    assert repository.stat().st_uid == 501
    assert sentinel.read_text() == "preserve"
    assert (sentinel.stat().st_uid, sentinel.stat().st_gid) == ownership


def test_nonempty_paths_are_not_silently_reowned(tmp_path: Path) -> None:
    module = _module()
    repository = tmp_path / "repository"
    repository.mkdir()
    sentinel = repository / "existing-data"
    sentinel.write_text("preserve")
    owner = repository.stat().st_uid
    if owner == 10001:
        pytest.skip("Fixture already belongs to the service")
    with pytest.raises(module.ConfigurationError, match="explicit ownership migration"):
        module.prepare_backup_paths(_configuration(tmp_path, ""))
    assert repository.stat().st_uid == owner
    assert sentinel.read_text() == "preserve"
    assert not (tmp_path / "rclone").exists()


def test_shared_paths_require_a_declared_common_group_before_creation(tmp_path: Path) -> None:
    module = _module()
    with pytest.raises(module.ConfigurationError, match="common service group"):
        module.prepare_backup_paths(_configuration(tmp_path, "mismatch"))
    assert not (tmp_path / "repository").exists()
    assert not (tmp_path / "rclone").exists()


@pytest.mark.parametrize("interactive", ["0", "1"])
def test_privileged_commands_are_displayed_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interactive: str,
) -> None:
    module = _module()
    monkeypatch.setenv("PREPARE_INTERACTIVE", interactive)
    monkeypatch.setattr(module.os, "geteuid", lambda: 99999)
    commands: list[list[str]] = []

    def run(command: list[str], *, check: bool, stdin: int) -> subprocess.CompletedProcess[bytes]:
        assert capsys.readouterr().err == "+ " + shlex.join(command) + "\n"
        assert command[:2] == (["sudo", "--"] if interactive == "1" else ["sudo", "-n"])
        assert "-p" not in command
        assert check and stdin == subprocess.DEVNULL
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    (tmp_path / "with spaces/repository").mkdir(parents=True)
    module.prepare_backup_paths(_configuration(tmp_path / "with spaces", ""))
    assert len(commands) == 1
    assert commands[0][-1] == str(tmp_path / "with spaces/repository")
    assert (tmp_path / "with spaces/repository").stat().st_uid == os.getuid()
