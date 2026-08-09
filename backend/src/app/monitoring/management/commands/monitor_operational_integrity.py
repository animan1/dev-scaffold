from __future__ import annotations

from time import sleep

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.monitoring.services import run_operational_checks, validate_monitoring_configuration


class Command(BaseCommand):
    help = "Run operational integrity checks and notify the site administrator."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval-seconds", type=int, default=900)

    def handle(
        self,
        *args: object,
        watch: bool = False,
        interval_seconds: int = 900,
        **options: object,
    ) -> None:
        if interval_seconds < 1:
            raise CommandError("--interval-seconds must be positive.")
        try:
            validate_monitoring_configuration()
        except ImproperlyConfigured as error:
            raise CommandError(str(error)) from error

        try:
            while True:
                results = run_operational_checks()
                for result in results:
                    status = "PASS" if result.healthy else "FAIL"
                    self.stdout.write(f"{status} {result.label}: {result.message}")
                if not watch:
                    return
                sleep(interval_seconds)
        except KeyboardInterrupt:
            self.stdout.write("Operational monitor stopped.")
