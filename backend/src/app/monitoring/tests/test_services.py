from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from organizations.utils import create_organization

from app.monitoring.checks import ACTIVE_USER_ORGANIZATION_CHECK
from app.monitoring.models import OperationalCheckState
from app.monitoring.services import run_operational_checks


@pytest.mark.django_db
def test_operational_monitor_notifies_only_on_failure_and_recovery(
    settings: object,
) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    settings.DEPLOYMENT_ENVIRONMENT = "production"  # type: ignore[attr-defined]
    settings.RELEASE_REVISION = "a" * 40  # type: ignore[attr-defined]
    user = User.objects.create_user(username="brewer")
    create_organization(user, "Cuplr Brewing")

    run_operational_checks()
    assert mail.outbox == []

    second_owner = User.objects.create_user(username="second-owner")
    second = create_organization(second_owner, "Second Brewery")
    second.add_user(user)
    run_operational_checks()
    run_operational_checks()

    assert [message.subject for message in mail.outbox] == [
        "[Cuplr production] Operational check failed: Active user organization count"
    ]
    assert "brewer (2)" in mail.outbox[0].body
    assert "Environment: production" in mail.outbox[0].body
    assert f"Release: {'a' * 40}" in mail.outbox[0].body

    second.is_active = False
    second.save(update_fields=["is_active"])
    second_owner.is_active = False
    second_owner.save(update_fields=["is_active"])
    run_operational_checks()

    assert [message.subject for message in mail.outbox] == [
        "[Cuplr production] Operational check failed: Active user organization count",
        "[Cuplr production] Operational check recovered: Active user organization count",
    ]
    assert (
        OperationalCheckState.objects.get(key=ACTIVE_USER_ORGANIZATION_CHECK).status
        == OperationalCheckState.Status.HEALTHY
    )


@pytest.mark.django_db
def test_operational_monitor_retries_a_failed_notification(settings: object) -> None:
    settings.SITE_ADMIN_EMAIL = "operator@example.com"  # type: ignore[attr-defined]
    User.objects.create_user(username="brewer")

    smtp_failure = patch(
        "app.monitoring.services.send_mail", side_effect=OSError("SMTP unavailable")
    )
    with smtp_failure:
        with pytest.raises(OSError, match="SMTP unavailable"):
            run_operational_checks()

    assert not OperationalCheckState.objects.exists()

    run_operational_checks()

    assert len(mail.outbox) == 1
    assert (
        OperationalCheckState.objects.get(key=ACTIVE_USER_ORGANIZATION_CHECK).status
        == OperationalCheckState.Status.FAILED
    )
