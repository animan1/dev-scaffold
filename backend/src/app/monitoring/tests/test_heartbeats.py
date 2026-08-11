from __future__ import annotations

import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.monitoring import heartbeats


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
    for channel in heartbeats.HeartbeatChannel:
        path = tmp_path / f"{channel.value}.url"
        path.write_text(f"https://monitor.example.test/ping/{channel.value}?token=secret\n")
        setting = heartbeats.heartbeat_url_file_setting(channel)
        environment[setting] = str(path)
        monkeypatch.setenv(setting, str(path))
    yield environment


@pytest.mark.parametrize(
    ("event", "suffix"),
    [
        (heartbeats.HeartbeatEvent.SUCCESS, "backup?token=secret"),
        (heartbeats.HeartbeatEvent.START, "backup/start?token=secret"),
        (heartbeats.HeartbeatEvent.FAILURE, "backup/fail?token=secret"),
    ],
)
def test_heartbeat_posts_no_content_and_preserves_query(
    event: heartbeats.HeartbeatEvent,
    suffix: str,
    heartbeat_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 10
        requests.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    heartbeats.send_heartbeat(heartbeats.HeartbeatChannel.BACKUP, event, heartbeat_environment)
    assert requests[0].full_url.endswith(suffix)
    assert requests[0].method == "POST"
    assert requests[0].data == b""


def test_transport_error_redacts_the_secret_url(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(request: urllib.request.Request, timeout: int) -> Response:
        raise OSError(f"{request.full_url} failed after {timeout}")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError) as raised:
        heartbeats.send_heartbeat(
            heartbeats.HeartbeatChannel.RESTORE, heartbeats.HeartbeatEvent.FAILURE
        )
    assert "secret" not in str(raised.value)
    assert "monitor.example.test" not in str(raised.value)


def test_non_success_response_is_an_error(
    heartbeat_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response(503))
    with pytest.raises(RuntimeError, match="heartbeat failed"):
        heartbeats.send_heartbeat(
            heartbeats.HeartbeatChannel.OPERATIONAL, heartbeats.HeartbeatEvent.SUCCESS
        )


def test_secret_must_be_a_small_https_file(tmp_path: Path) -> None:
    setting = heartbeats.heartbeat_url_file_setting(heartbeats.HeartbeatChannel.OPERATIONAL)
    with pytest.raises(heartbeats.HeartbeatConfigurationError, match="must name"):
        heartbeats.load_heartbeat_url(heartbeats.HeartbeatChannel.OPERATIONAL, {})
    with pytest.raises(heartbeats.HeartbeatConfigurationError, match="cannot read"):
        heartbeats.load_heartbeat_url(
            heartbeats.HeartbeatChannel.OPERATIONAL, {setting: str(tmp_path / "missing")}
        )
    path = tmp_path / "url"
    path.write_text("http://monitor.example.test/ping/id")
    with pytest.raises(heartbeats.HeartbeatConfigurationError, match="must be HTTPS"):
        heartbeats.load_heartbeat_url(heartbeats.HeartbeatChannel.OPERATIONAL, {setting: str(path)})
    path.write_text("x" * (heartbeats.MAX_URL_FILE_BYTES + 1))
    with pytest.raises(heartbeats.HeartbeatConfigurationError, match="too large"):
        heartbeats.load_heartbeat_url(heartbeats.HeartbeatChannel.OPERATIONAL, {setting: str(path)})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MONITOR_OPERATIONAL_INTERVAL_SECONDS", "59"),
        ("MONITOR_OPERATIONAL_INTERVAL_SECONDS", "604801"),
        ("MONITOR_OPERATIONAL_GRACE_SECONDS", "59"),
        ("MONITOR_OPERATIONAL_GRACE_SECONDS", "86401"),
        ("MONITOR_OPERATIONAL_INTERVAL_SECONDS", "daily"),
    ],
)
def test_provider_policy_is_bounded(key: str, value: str) -> None:
    with pytest.raises(heartbeats.HeartbeatConfigurationError):
        heartbeats.load_heartbeat_policy(heartbeats.HeartbeatChannel.OPERATIONAL, {key: value})
