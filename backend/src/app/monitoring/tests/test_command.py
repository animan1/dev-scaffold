from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from organizations.utils import create_organization


@pytest.mark.django_db
def test_monitor_command_runs_checks_once(
    settings: object, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    user = User.objects.create_user(username="brewer")
    create_organization(user, "Cuplr Brewing")

    call_command("monitor_operational_integrity")

    assert "PASS Active user organization count" in capsys.readouterr().out


def test_monitor_command_requires_notification_configuration(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = ""  # type: ignore[attr-defined]

    with pytest.raises(CommandError, match=r"CUPLR_SITE_ADMIN_EMAIL in deploy/\.env\.prod"):
        call_command("monitor_operational_integrity")


def test_monitor_command_rejects_console_email_in_production(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    settings.DEBUG = False  # type: ignore[attr-defined]
    settings.EMAIL_BACKEND = (  # type: ignore[attr-defined]
        "django.core.mail.backends.console.EmailBackend"
    )

    with pytest.raises(CommandError, match="production email backend"):
        call_command("monitor_operational_integrity")


def test_monitor_command_identifies_where_to_set_smtp_host(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    settings.EMAIL_BACKEND = (  # type: ignore[attr-defined]
        "django.core.mail.backends.smtp.EmailBackend"
    )
    settings.EMAIL_HOST = ""  # type: ignore[attr-defined]

    with pytest.raises(CommandError, match=r"DJANGO_EMAIL_HOST in deploy/\.env\.prod"):
        call_command("monitor_operational_integrity")


def test_monitor_command_rejects_invalid_interval(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]

    with pytest.raises(CommandError, match="positive"):
        call_command("monitor_operational_integrity", interval_seconds=0)


@pytest.mark.django_db
def test_monitor_command_repeats_in_watch_mode(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]

    with patch(
        "app.monitoring.management.commands.monitor_operational_integrity.sleep",
        side_effect=KeyboardInterrupt,
    ) as wait:
        call_command("monitor_operational_integrity", watch=True, interval_seconds=30)

    wait.assert_called_once_with(30)
