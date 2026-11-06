from __future__ import annotations

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
@pytest.mark.parametrize(
    "sharing,mode",
    [("", 0o700), ("read", 0o2750), ("write", 0o2770), ("supplementary", 0o2750)],
)
def test_prepared_paths_use_fixed_uid_and_required_group_access(
    tmp_path: Path,
    sharing: str,
    mode: int,
) -> None:
    prepare = _module().prepare_backup_paths
    config = _configuration(tmp_path, sharing)
    credential = tmp_path / "rclone/rclone.conf"
    credential.parent.mkdir()
    credential.write_text("existing-provider-credential")
    prepare(config)
    repository = tmp_path / "repository"
    info = repository.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (10001, 10001, mode)
    assert (tmp_path / "rclone").stat().st_mode & 0o7777 == 0o700
    assert credential.read_text() == "existing-provider-credential"
    assert (credential.stat().st_uid, credential.stat().st_gid) == (10001, 10001)
    assert credential.stat().st_mode & 0o7777 == 0o600
    subprocess.run(["chown", "10001:10001", str(repository)], check=True)
    assert repository.stat().st_mode & 0o7777 == mode
    sentinel = repository / "existing-data"
    sentinel.write_text("preserve")
    ownership = (sentinel.stat().st_uid, sentinel.stat().st_gid)
    repository.chmod(0o755)
    prepare(config)
    assert repository.stat().st_mode & 0o7777 == mode
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
    monkeypatch.setattr(module.os, "geteuid", lambda: 501)
    commands: list[list[str]] = []

    def run(command: list[str], *, check: bool, stdin: int) -> subprocess.CompletedProcess[bytes]:
        assert capsys.readouterr().err == "+ " + shlex.join(command) + "\n"
        assert command[:2] == (["sudo", "--"] if interactive == "1" else ["sudo", "-n"])
        assert "-p" not in command
        assert check and stdin == subprocess.DEVNULL
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.prepare_backup_paths(_configuration(tmp_path / "with spaces", ""))
    assert len(commands) == 2
    assert commands[0][-1] == str(tmp_path / "with spaces/repository")
    assert not (tmp_path / "with spaces").exists()
