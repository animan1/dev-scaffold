from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from app.monitoring.checks import OperationalCheckResult
from app.monitoring.models import OperationalCheckState
from app.monitoring.services import run_operational_checks, validate_monitoring_configuration


def result(healthy: bool) -> OperationalCheckResult:
    return OperationalCheckResult("generic-check", "Generic check", healthy, "details")


@pytest.mark.django_db
def test_monitor_notifies_only_on_failure_and_recovery(settings: object) -> None:
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
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    with (
        patch("app.monitoring.services.operational_checks", return_value=(result(False),)),
        patch("app.monitoring.services.send_mail", side_effect=OSError("SMTP unavailable")),
        pytest.raises(OSError, match="SMTP unavailable"),
    ):
        run_operational_checks()
    assert not OperationalCheckState.objects.exists()


def test_email_configuration_is_validated(settings: object) -> None:
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
