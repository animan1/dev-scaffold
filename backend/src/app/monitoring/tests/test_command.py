from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.monitoring.checks import OperationalCheckResult


def healthy_result() -> OperationalCheckResult:
    return OperationalCheckResult("generic", "Generic", True, "healthy")


def test_monitor_command_runs_cuplr_interface_once(
    settings: object, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with patch(
        "app.monitoring.management.commands.monitor_operational_integrity.run_operational_checks",
        return_value=(healthy_result(),),
    ):
        call_command("monitor_operational_integrity")
    assert "PASS Generic: healthy" in capsys.readouterr().out


def test_monitor_command_rejects_nonpositive_interval(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with pytest.raises(CommandError, match="positive"):
        call_command("monitor_operational_integrity", interval_seconds=0)


def test_monitor_command_reports_configuration_error(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = ""  # type: ignore[attr-defined]
    with pytest.raises(CommandError, match="SITE_ADMIN_EMAIL"):
        call_command("monitor_operational_integrity")


def test_monitor_command_repeats_in_watch_mode(
    settings: object, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with (
        patch(
            "app.monitoring.management.commands.monitor_operational_integrity.run_operational_checks",
            return_value=(healthy_result(),),
        ),
        patch(
            "app.monitoring.management.commands.monitor_operational_integrity.sleep",
            side_effect=KeyboardInterrupt,
        ) as wait,
    ):
        call_command("monitor_operational_integrity", watch=True, interval_seconds=300)
    wait.assert_called_once_with(300)
    assert "Operational monitor stopped" in capsys.readouterr().out
