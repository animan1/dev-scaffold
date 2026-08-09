"""Content-free heartbeats for an external dead-man monitoring provider."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class Channel(StrEnum):
    OPERATIONAL = "operational"
    BACKUP = "backup"
    RESTORE = "restore"


class Event(StrEnum):
    START = "start"
    SUCCESS = "success"
    FAILURE = "failure"


class ConfigurationError(ValueError):
    """Raised for invalid or incomplete dead-man configuration."""


@dataclass(frozen=True)
class Policy:
    interval_seconds: int
    grace_seconds: int


DEFAULT_POLICIES: Mapping[Channel, Policy] = {
    Channel.OPERATIONAL: Policy(interval_seconds=300, grace_seconds=600),
    Channel.BACKUP: Policy(interval_seconds=86_400, grace_seconds=21_600),
    Channel.RESTORE: Policy(interval_seconds=604_800, grace_seconds=86_400),
}
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 604_800
MIN_GRACE_SECONDS = 60
MAX_GRACE_SECONDS = 86_400
MAX_URL_FILE_BYTES = 4096


def _prefix(channel: Channel) -> str:
    return f"DEADMAN_{channel.value.upper()}"


def load_policy(channel: Channel, environ: Mapping[str, str] = os.environ) -> Policy:
    """Load and bound the provider schedule expected for one channel."""
    default = DEFAULT_POLICIES[channel]
    prefix = _prefix(channel)
    try:
        policy = Policy(
            interval_seconds=int(
                environ.get(f"{prefix}_INTERVAL_SECONDS", default.interval_seconds)
            ),
            grace_seconds=int(environ.get(f"{prefix}_GRACE_SECONDS", default.grace_seconds)),
        )
    except ValueError as error:
        raise ConfigurationError(f"{channel.value} interval and grace must be integers") from error

    if not MIN_INTERVAL_SECONDS <= policy.interval_seconds <= MAX_INTERVAL_SECONDS:
        raise ConfigurationError(
            f"{channel.value} interval must be between {MIN_INTERVAL_SECONDS} and "
            f"{MAX_INTERVAL_SECONDS} seconds"
        )
    if not MIN_GRACE_SECONDS <= policy.grace_seconds <= MAX_GRACE_SECONDS:
        raise ConfigurationError(
            f"{channel.value} grace must be between {MIN_GRACE_SECONDS} and "
            f"{MAX_GRACE_SECONDS} seconds"
        )
    return policy


def load_url(channel: Channel, environ: Mapping[str, str] = os.environ) -> str:
    """Read a provider URL from its mounted secret file."""
    variable = f"{_prefix(channel)}_URL_FILE"
    filename = environ.get(variable)
    if not filename:
        raise ConfigurationError(f"{variable} must name a mounted secret file")

    path = Path(filename)
    try:
        if path.stat().st_size > MAX_URL_FILE_BYTES:
            raise ConfigurationError(f"{channel.value} heartbeat URL file is too large")
        url = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigurationError(f"cannot read the {channel.value} heartbeat URL file") from error

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ConfigurationError(
            f"{channel.value} heartbeat URL must be an HTTPS URL without a fragment"
        )
    return url


def event_url(url: str, event: Event) -> str:
    """Add Healthchecks-compatible lifecycle suffixes without disturbing a query string."""
    if event is Event.SUCCESS:
        return url
    suffix = "start" if event is Event.START else "fail"
    parsed = urlsplit(url)
    path = f"{parsed.path.rstrip('/')}/{suffix}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def send_heartbeat(
    channel: Channel,
    event: Event,
    environ: Mapping[str, str] = os.environ,
) -> None:
    """POST a zero-byte heartbeat without exposing its secret URL in errors."""
    request = urllib.request.Request(
        event_url(load_url(channel, environ), event),
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


def run_command(channel: Channel, command: Sequence[str]) -> int:
    if not command:
        raise ConfigurationError("run requires a command after --")
    _send_best_effort(channel, Event.START)
    try:
        result = subprocess.run(command, check=False)
    except OSError:
        _send_best_effort(channel, Event.FAILURE)
        return 127
    _send_best_effort(channel, Event.SUCCESS if result.returncode == 0 else Event.FAILURE)
    return result.returncode


def _send_best_effort(channel: Channel, event: Event) -> None:
    """Keep monitoring-provider outages from changing the wrapped job result."""
    try:
        send_heartbeat(channel, event)
    except (ConfigurationError, RuntimeError) as error:
        print(f"deadman: warning: {error}", file=sys.stderr)


def watch(channel: Channel) -> None:
    policy = load_policy(channel)
    while True:
        send_heartbeat(channel, Event.SUCCESS)
        time.sleep(policy.interval_seconds)


def check_configuration() -> None:
    for channel in Channel:
        load_url(channel)
        load_policy(channel)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    ping_parser = subparsers.add_parser("ping", help="send one lifecycle heartbeat")
    ping_parser.add_argument("channel", choices=list(Channel), type=Channel)
    ping_parser.add_argument("event", choices=list(Event), type=Event)

    watch_parser = subparsers.add_parser("watch", help="send recurring success heartbeats")
    watch_parser.add_argument("channel", choices=list(Channel), type=Channel)

    run_parser = subparsers.add_parser("run", help="wrap a command with lifecycle heartbeats")
    run_parser.add_argument("channel", choices=list(Channel), type=Channel)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    subparsers.add_parser("check", help="validate all heartbeat secrets and policies")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "ping":
            send_heartbeat(args.channel, args.event)
        elif args.action == "watch":
            watch(args.channel)
        elif args.action == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            return run_command(args.channel, command)
        else:
            check_configuration()
    except (ConfigurationError, RuntimeError) as error:
        print(f"deadman: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
