from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from app.monitoring.checks import OperationalCheckResult
from app.monitoring.heartbeats import (
    HeartbeatChannel,
    HeartbeatConfigurationError,
    HeartbeatEvent,
)
from app.monitoring.models import OperationalCheckState
from app.monitoring.services import run_operational_checks, validate_monitoring_configuration


def result(healthy: bool) -> OperationalCheckResult:
    return OperationalCheckResult("generic-check", "Generic check", healthy, "details")


@pytest.mark.django_db
def test_monitor_notifies_only_on_failure_and_recovery(settings: object) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "email"  # type: ignore[attr-defined]
    settings.MONITOR_SITE_NAME = "Example"  # type: ignore[attr-defined]
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with patch("app.monitoring.services.operational_checks", return_value=(result(True),)):
        run_operational_checks()
    with patch("app.monitoring.services.operational_checks", return_value=(result(False),)):
        run_operational_checks()
        run_operational_checks()
    with patch("app.monitoring.services.operational_checks", return_value=(result(True),)):
        run_operational_checks()

    assert [message.subject for message in mail.outbox] == [
        "[Example development] Operational check failed: Generic check",
        "[Example development] Operational check recovered: Generic check",
    ]
    assert "Release: unknown" in mail.outbox[0].body
    assert str(OperationalCheckState.objects.get()) == "Generic check: healthy"


@pytest.mark.django_db
def test_failed_notification_is_retried(settings: object) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "email"  # type: ignore[attr-defined]
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with (
        patch("app.monitoring.services.operational_checks", return_value=(result(False),)),
        patch("app.monitoring.services.send_mail", side_effect=OSError("SMTP unavailable")),
        pytest.raises(OSError, match="SMTP unavailable"),
    ):
        run_operational_checks()
    assert not OperationalCheckState.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("healthy", "event"),
    [(True, HeartbeatEvent.SUCCESS), (False, HeartbeatEvent.FAILURE)],
)
def test_external_backend_reports_aggregate_health(
    healthy: bool, event: HeartbeatEvent, settings: object
) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "external"  # type: ignore[attr-defined]
    with (
        patch("app.monitoring.services.operational_checks", return_value=(result(healthy),)),
        patch("app.monitoring.services.send_heartbeat") as send,
    ):
        run_operational_checks()
    send.assert_called_once_with(HeartbeatChannel.OPERATIONAL, event)


def test_email_configuration_is_validated(settings: object) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "email"  # type: ignore[attr-defined]
    settings.SITE_ADMIN_EMAIL = ""  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="SITE_ADMIN_EMAIL"):
        validate_monitoring_configuration()
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    settings.DEBUG = False  # type: ignore[attr-defined]
    settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="production email backend"):
        validate_monitoring_configuration()
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # type: ignore[attr-defined]
    settings.EMAIL_HOST = ""  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="DJANGO_EMAIL_HOST"):
        validate_monitoring_configuration()


def test_unknown_notification_backend_is_rejected(settings: object) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "unknown"  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="must be email or external"):
        validate_monitoring_configuration()


def test_external_configuration_validates_every_channel(settings: object) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "external"  # type: ignore[attr-defined]
    with (
        patch("app.monitoring.services.load_heartbeat_url") as load_url,
        patch("app.monitoring.services.load_heartbeat_policy") as load_policy,
    ):
        validate_monitoring_configuration()
    assert load_url.call_count == 3
    assert load_policy.call_count == 3


def test_external_configuration_error_becomes_django_configuration_error(
    settings: object,
) -> None:
    settings.MONITOR_NOTIFICATION_BACKEND = "external"  # type: ignore[attr-defined]
    with (
        patch(
            "app.monitoring.services.load_heartbeat_url",
            side_effect=HeartbeatConfigurationError("bad heartbeat secret"),
        ),
        pytest.raises(Exception, match="bad heartbeat secret"),
    ):
        validate_monitoring_configuration()
