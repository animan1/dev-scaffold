from __future__ import annotations

import subprocess

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.monitoring.heartbeats import HeartbeatEvent


@pytest.mark.parametrize("returncode", [0, 23])
def test_monitor_job_preserves_command_status(
    returncode: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[HeartbeatEvent] = []
    monkeypatch.setattr(
        "app.monitoring.management.commands.monitor_job.send_heartbeat",
        lambda _channel, event: events.append(event),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, returncode),
    )
    if returncode:
        with pytest.raises(SystemExit) as raised:
            call_command("monitor_job", "backup", "--", "backup-command")
        assert raised.value.code == returncode
    else:
        call_command("monitor_job", "backup", "--", "backup-command")
    assert events == [
        HeartbeatEvent.START,
        HeartbeatEvent.SUCCESS if returncode == 0 else HeartbeatEvent.FAILURE,
    ]


def test_monitor_job_requires_command() -> None:
    with pytest.raises(CommandError, match="after --"):
        call_command("monitor_job", "restore")


def test_monitor_job_reports_unstartable_command(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[HeartbeatEvent] = []
    monkeypatch.setattr(
        "app.monitoring.management.commands.monitor_job.send_heartbeat",
        lambda _channel, event: events.append(event),
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError))
    with pytest.raises(SystemExit) as raised:
        call_command("monitor_job", "restore", "--", "missing")
    assert raised.value.code == 127
    assert events == [HeartbeatEvent.START, HeartbeatEvent.FAILURE]


def test_monitoring_outage_does_not_change_job_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "app.monitoring.management.commands.monitor_job.send_heartbeat",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("redacted provider failure")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, 0),
    )
    call_command("monitor_job", "backup", "--", "backup-command")
    assert "redacted provider failure" in capsys.readouterr().err
