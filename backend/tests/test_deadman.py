from __future__ import annotations

import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import deadman


class Response:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def heartbeat_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    environment: dict[str, str] = {}
    for channel in deadman.Channel:
        path = tmp_path / f"{channel.value}.url"
        path.write_text(f"https://monitor.example.test/ping/{channel.value}?token=secret\n")
        key = f"DEADMAN_{channel.value.upper()}_URL_FILE"
        environment[key] = str(path)
        monkeypatch.setenv(key, str(path))
    yield environment


def test_success_heartbeat_posts_no_content(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 10
        requests.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    deadman.send_heartbeat(deadman.Channel.BACKUP, deadman.Event.SUCCESS, heartbeat_environment)

    assert requests[0].full_url == "https://monitor.example.test/ping/backup?token=secret"
    assert requests[0].method == "POST"
    assert requests[0].data == b""


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (deadman.Event.START, "https://monitor.example.test/ping/restore/start?token=secret"),
        (deadman.Event.FAILURE, "https://monitor.example.test/ping/restore/fail?token=secret"),
    ],
)
def test_lifecycle_suffix_precedes_query_string(event: deadman.Event, expected: str) -> None:
    assert (
        deadman.event_url("https://monitor.example.test/ping/restore?token=secret", event)
        == expected
    )


def test_secret_url_is_redacted_from_transport_errors(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(request: urllib.request.Request, timeout: int) -> Response:
        raise OSError(f"cannot connect to {request.full_url} after {timeout}")

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    with pytest.raises(RuntimeError) as raised:
        deadman.send_heartbeat(
            deadman.Channel.OPERATIONAL, deadman.Event.FAILURE, heartbeat_environment
        )

    assert "secret" not in str(raised.value)
    assert "monitor.example.test" not in str(raised.value)


def test_plain_http_secret_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    secret.write_text("http://monitor.example.test/ping/id")

    with pytest.raises(deadman.ConfigurationError, match="must be an HTTPS URL"):
        deadman.load_url(
            deadman.Channel.OPERATIONAL,
            {"DEADMAN_OPERATIONAL_URL_FILE": str(secret)},
        )


def test_missing_and_unreadable_secret_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(deadman.ConfigurationError, match="must name a mounted secret file"):
        deadman.load_url(deadman.Channel.OPERATIONAL, {})

    with pytest.raises(deadman.ConfigurationError, match="cannot read"):
        deadman.load_url(
            deadman.Channel.OPERATIONAL,
            {"DEADMAN_OPERATIONAL_URL_FILE": str(tmp_path / "missing")},
        )


def test_oversized_secret_file_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "url"
    secret.write_text("x" * (deadman.MAX_URL_FILE_BYTES + 1))

    with pytest.raises(deadman.ConfigurationError, match="too large"):
        deadman.load_url(
            deadman.Channel.OPERATIONAL,
            {"DEADMAN_OPERATIONAL_URL_FILE": str(secret)},
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DEADMAN_OPERATIONAL_INTERVAL_SECONDS", "59"),
        ("DEADMAN_OPERATIONAL_INTERVAL_SECONDS", "604801"),
        ("DEADMAN_OPERATIONAL_GRACE_SECONDS", "59"),
        ("DEADMAN_OPERATIONAL_GRACE_SECONDS", "86401"),
    ],
)
def test_provider_policy_is_bounded(key: str, value: str) -> None:
    with pytest.raises(deadman.ConfigurationError):
        deadman.load_policy(deadman.Channel.OPERATIONAL, {key: value})


def test_provider_policy_must_use_integers() -> None:
    with pytest.raises(deadman.ConfigurationError, match="must be integers"):
        deadman.load_policy(
            deadman.Channel.OPERATIONAL,
            {"DEADMAN_OPERATIONAL_INTERVAL_SECONDS": "often"},
        )


def test_non_success_provider_response_is_an_error(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response(503))

    with pytest.raises(RuntimeError, match="heartbeat failed"):
        deadman.send_heartbeat(deadman.Channel.OPERATIONAL, deadman.Event.SUCCESS)


@pytest.mark.parametrize("returncode", [0, 23])
def test_run_command_preserves_exit_code_and_reports_outcome(
    returncode: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[deadman.Event] = []
    monkeypatch.setattr(
        deadman,
        "send_heartbeat",
        lambda _channel, event: events.append(event),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, returncode),
    )

    assert deadman.run_command(deadman.Channel.RESTORE, ["verify-restore"]) == returncode
    assert events == [
        deadman.Event.START,
        deadman.Event.SUCCESS if returncode == 0 else deadman.Event.FAILURE,
    ]


def test_run_command_requires_a_command() -> None:
    with pytest.raises(deadman.ConfigurationError, match="requires a command"):
        deadman.run_command(deadman.Channel.BACKUP, [])


def test_run_command_reports_an_unstartable_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[deadman.Event] = []
    monkeypatch.setattr(
        deadman,
        "send_heartbeat",
        lambda _channel, event: events.append(event),
    )

    def fail_to_start(_command: object, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        raise OSError

    monkeypatch.setattr(subprocess, "run", fail_to_start)

    assert deadman.run_command(deadman.Channel.BACKUP, ["missing-command"]) == 127
    assert events == [deadman.Event.START, deadman.Event.FAILURE]


def test_provider_outage_does_not_change_wrapped_command_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        deadman,
        "send_heartbeat",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("redacted provider failure")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, 0),
    )

    assert deadman.run_command(deadman.Channel.BACKUP, ["backup-command"]) == 0
    assert "warning: redacted provider failure" in capsys.readouterr().err


def test_watch_uses_the_channel_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[deadman.Event] = []
    monkeypatch.setattr(
        deadman,
        "send_heartbeat",
        lambda _channel, event: events.append(event),
    )

    def stop_after_first_sleep(seconds: int) -> None:
        assert seconds == deadman.DEFAULT_POLICIES[deadman.Channel.OPERATIONAL].interval_seconds
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", stop_after_first_sleep)

    with pytest.raises(KeyboardInterrupt):
        deadman.watch(deadman.Channel.OPERATIONAL)
    assert events == [deadman.Event.SUCCESS]


def test_success_after_failure_supports_provider_recovery_notices(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 10
        urls.append(request.full_url)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    deadman.send_heartbeat(deadman.Channel.BACKUP, deadman.Event.FAILURE)
    deadman.send_heartbeat(deadman.Channel.BACKUP, deadman.Event.SUCCESS)

    assert urls == [
        "https://monitor.example.test/ping/backup/fail?token=secret",
        "https://monitor.example.test/ping/backup?token=secret",
    ]


def test_check_validates_all_three_secret_files(heartbeat_environment: dict[str, str]) -> None:
    deadman.check_configuration()


@pytest.mark.parametrize(
    ("arguments", "called"),
    [
        (["ping", "backup", "success"], "ping"),
        (["watch", "operational"], "watch"),
        (["run", "restore", "--", "verify"], "run"),
        (["check"], "check"),
    ],
)
def test_cli_dispatches_actions(
    arguments: list[str], called: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(deadman, "send_heartbeat", lambda *_args: calls.append("ping"))
    monkeypatch.setattr(deadman, "watch", lambda *_args: calls.append("watch"))

    def fake_run(_channel: deadman.Channel, command: list[str]) -> int:
        calls.append("run")
        return 0 if command == ["verify"] else 1

    monkeypatch.setattr(deadman, "run_command", fake_run)
    monkeypatch.setattr(deadman, "check_configuration", lambda: calls.append("check"))

    assert deadman.main(arguments) == 0
    assert calls == [called]


def test_cli_redacts_configuration_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert deadman.main(["ping", "backup", "success"]) == 1
    assert "DEADMAN_BACKUP_URL_FILE" in capsys.readouterr().err
