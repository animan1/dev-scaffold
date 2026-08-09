from __future__ import annotations

import argparse
import subprocess

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.monitoring.heartbeats import HeartbeatChannel, HeartbeatEvent, send_heartbeat


class Command(BaseCommand):
    help = "Wrap a backup or restore-verification job with external heartbeats."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("channel", choices=("backup", "restore"), type=HeartbeatChannel)
        parser.add_argument("command", nargs=argparse.REMAINDER)

    def handle(
        self,
        *args: object,
        channel: HeartbeatChannel,
        command: list[str],
        **options: object,
    ) -> None:
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            raise CommandError("Provide a command after --.")

        self._send_best_effort(channel, HeartbeatEvent.START)
        try:
            result = subprocess.run(command, check=False)
        except OSError:
            self._send_best_effort(channel, HeartbeatEvent.FAILURE)
            raise SystemExit(127) from None
        event = HeartbeatEvent.SUCCESS if result.returncode == 0 else HeartbeatEvent.FAILURE
        self._send_best_effort(channel, event)
        if result.returncode:
            raise SystemExit(result.returncode)

    def _send_best_effort(self, channel: HeartbeatChannel, event: HeartbeatEvent) -> None:
        try:
            send_heartbeat(channel, event)
        except (RuntimeError, ValueError) as error:
            self.stderr.write(f"Monitoring warning: {error}")
