from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_URL_FILE_BYTES = 4096
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 604_800
MIN_GRACE_SECONDS = 60
MAX_GRACE_SECONDS = 86_400


class HeartbeatChannel(StrEnum):
    OPERATIONAL = "operational"
    BACKUP = "backup"
    RESTORE = "restore"


class HeartbeatEvent(StrEnum):
    START = "start"
    SUCCESS = "success"
    FAILURE = "failure"


class HeartbeatConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class HeartbeatPolicy:
    interval_seconds: int
    grace_seconds: int


DEFAULT_POLICIES: Mapping[HeartbeatChannel, HeartbeatPolicy] = {
    HeartbeatChannel.OPERATIONAL: HeartbeatPolicy(300, 600),
    HeartbeatChannel.BACKUP: HeartbeatPolicy(86_400, 21_600),
    HeartbeatChannel.RESTORE: HeartbeatPolicy(604_800, 86_400),
}


def heartbeat_url_file_setting(channel: HeartbeatChannel) -> str:
    return f"MONITOR_{channel.value.upper()}_URL_FILE"


def load_heartbeat_policy(
    channel: HeartbeatChannel, environ: Mapping[str, str] = os.environ
) -> HeartbeatPolicy:
    prefix = f"MONITOR_{channel.value.upper()}"
    default = DEFAULT_POLICIES[channel]
    try:
        policy = HeartbeatPolicy(
            interval_seconds=int(
                environ.get(f"{prefix}_INTERVAL_SECONDS", default.interval_seconds)
            ),
            grace_seconds=int(environ.get(f"{prefix}_GRACE_SECONDS", default.grace_seconds)),
        )
    except ValueError as error:
        raise HeartbeatConfigurationError(
            f"{channel.value} interval and grace must be integers"
        ) from error
    if not MIN_INTERVAL_SECONDS <= policy.interval_seconds <= MAX_INTERVAL_SECONDS:
        raise HeartbeatConfigurationError(
            f"{channel.value} interval must be between {MIN_INTERVAL_SECONDS} and "
            f"{MAX_INTERVAL_SECONDS} seconds"
        )
    if not MIN_GRACE_SECONDS <= policy.grace_seconds <= MAX_GRACE_SECONDS:
        raise HeartbeatConfigurationError(
            f"{channel.value} grace must be between {MIN_GRACE_SECONDS} and "
            f"{MAX_GRACE_SECONDS} seconds"
        )
    return policy


def load_heartbeat_url(channel: HeartbeatChannel, environ: Mapping[str, str] = os.environ) -> str:
    setting = heartbeat_url_file_setting(channel)
    filename = environ.get(setting)
    if not filename:
        raise HeartbeatConfigurationError(f"{setting} must name a mounted secret file")
    path = Path(filename)
    try:
        if path.stat().st_size > MAX_URL_FILE_BYTES:
            raise HeartbeatConfigurationError(f"{channel.value} heartbeat URL file is too large")
        url = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise HeartbeatConfigurationError(
            f"cannot read the {channel.value} heartbeat URL file"
        ) from error
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise HeartbeatConfigurationError(
            f"{channel.value} heartbeat URL must be HTTPS and have no fragment"
        )
    return url


def _event_url(url: str, event: HeartbeatEvent) -> str:
    if event is HeartbeatEvent.SUCCESS:
        return url
    suffix = "start" if event is HeartbeatEvent.START else "fail"
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"{parsed.path.rstrip('/')}/{suffix}", parsed.query, "")
    )


def send_heartbeat(
    channel: HeartbeatChannel,
    event: HeartbeatEvent,
    environ: Mapping[str, str] = os.environ,
) -> None:
    request = urllib.request.Request(
        _event_url(load_heartbeat_url(channel, environ), event),
        data=b"",
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError
    except (OSError, RuntimeError, urllib.error.URLError) as error:
        raise RuntimeError(
            f"external {channel.value} heartbeat failed for event {event.value}"
        ) from error
