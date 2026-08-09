from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from organizations.utils import create_organization

from app.integrations.models import GoogleCalendarConnection
from app.integrations.services import (
    GoogleCalendarConfigurationError,
    GoogleCalendarOption,
)
from app.monitoring.checks import (
    active_user_organization_check,
    database_backup_freshness_check,
    database_backup_verification_freshness_check,
    google_calendar_duplicate_check,
    google_calendar_installation_readiness_check,
)


@pytest.mark.django_db
def test_active_user_organization_check_passes_for_one_active_organization() -> None:
    user = User.objects.create_user(username="brewer")
    create_organization(user, "Cuplr Brewing")

    result = active_user_organization_check()

    assert result.healthy is True
    expected = "Every active user belongs to exactly one active organization."
    assert result.message == expected


@pytest.mark.django_db
@pytest.mark.parametrize("organization_count", [0, 2])
def test_active_user_organization_check_reports_unsupported_counts(
    organization_count: int,
) -> None:
    user = User.objects.create_user(username="brewer")
    for index in range(organization_count):
        organization = create_organization(
            User.objects.create_user(username=f"owner-{index}"),
            f"Brewery {index}",
        )
        organization.add_user(user)

    result = active_user_organization_check()

    assert result.healthy is False
    assert f"brewer ({organization_count})" in result.message


@pytest.mark.django_db
def test_active_user_organization_check_ignores_inactive_users() -> None:
    User.objects.create_user(username="former-brewer", is_active=False)

    assert active_user_organization_check().healthy is True


@override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET="")
def test_google_calendar_readiness_reports_missing_deployment_settings() -> None:
    result = google_calendar_installation_readiness_check()

    assert result.healthy is False
    assert result.message == (
        "Set these deployment settings: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET."
    )


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="client-id",
    GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
)
def test_google_calendar_readiness_passes_without_exposing_credentials() -> None:
    result = google_calendar_installation_readiness_check()

    assert result.healthy is True
    assert result.message == "Google Calendar OAuth configuration is ready."


@pytest.mark.django_db
def test_google_calendar_duplicate_check_reports_calendar_ids() -> None:
    owner = User.objects.create_user(username="owner")
    organization = create_organization(owner, "Cuplr Brewing")
    connection_fields = {"organization": organization, "refresh_token": "token"}
    GoogleCalendarConnection.objects.create(**connection_fields)

    with patch(
        "app.monitoring.checks.duplicate_cuplr_google_calendars",
        return_value=[
            GoogleCalendarOption("selected@example.com", "Cuplr Brewing schedule"),
            GoogleCalendarOption("orphaned@example.com", "Cuplr Brewing schedule"),
        ],
    ):
        result = google_calendar_duplicate_check()

    assert result.healthy is False
    assert result.message == (
        "Google Calendar needs reconciliation. Cuplr Brewing: "
        "selected@example.com, orphaned@example.com."
    )


@pytest.mark.django_db
def test_google_calendar_duplicate_check_reports_inspection_failure() -> None:
    owner = User.objects.create_user(username="owner")
    organization = create_organization(owner, "Cuplr Brewing")
    connection_fields = {"organization": organization, "refresh_token": "token"}
    GoogleCalendarConnection.objects.create(**connection_fields)

    with patch(
        "app.monitoring.checks.duplicate_cuplr_google_calendars",
        side_effect=GoogleCalendarConfigurationError("Reconnect Google Calendar."),
    ):
        result = google_calendar_duplicate_check()

    assert result.healthy is False
    expected = "Cuplr Brewing: inspection failed (Reconnect Google Calendar.)"
    assert expected in result.message


@pytest.mark.django_db
def test_google_calendar_duplicate_check_passes_without_duplicates() -> None:
    assert google_calendar_duplicate_check().healthy is True


@pytest.mark.django_db
@override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET="")
def test_google_calendar_duplicate_check_waits_for_installation_readiness() -> None:
    result = google_calendar_duplicate_check()

    assert result.healthy is True
    expected = "Duplicate inspection is waiting for Google Calendar configuration."
    assert result.message == expected


@override_settings(BACKUP_MAX_AGE_SECONDS=90000)
def test_database_backup_checks_pass_for_current_success_markers(
    tmp_path: Path,
) -> None:
    timestamp = int(timezone.now().timestamp())
    (tmp_path / "last-backup").write_text(str(timestamp))
    (tmp_path / "last-restore-verification").write_text(str(timestamp))

    with override_settings(BACKUP_STATUS_DIR=str(tmp_path), RELEASE_DEPLOYED_AT=""):
        backup = database_backup_freshness_check()
        verification = database_backup_verification_freshness_check()

    assert backup.healthy is True
    assert verification.healthy is True


@override_settings(BACKUP_MAX_AGE_SECONDS=90000)
def test_database_backup_checks_report_missing_and_stale_successes(
    tmp_path: Path,
) -> None:
    stale_timestamp = int((timezone.now() - timedelta(hours=26)).timestamp())
    (tmp_path / "last-backup").write_text(str(stale_timestamp))

    with override_settings(BACKUP_STATUS_DIR=str(tmp_path), RELEASE_DEPLOYED_AT=""):
        backup = database_backup_freshness_check()
        verification = database_backup_verification_freshness_check()

    assert backup.healthy is False
    assert "is stale" in backup.message
    assert verification.healthy is False
    assert verification.message == (
        "No successful database backup restore verification has been recorded."
    )


@override_settings(BACKUP_MAX_AGE_SECONDS=172800)
def test_missing_backup_markers_wait_for_deployment_grace(tmp_path: Path) -> None:
    recent_deployment = (timezone.now() - timedelta(minutes=5)).isoformat()

    deployment_settings = override_settings(
        BACKUP_STATUS_DIR=str(tmp_path), RELEASE_DEPLOYED_AT=recent_deployment
    )
    with deployment_settings:
        backup = database_backup_freshness_check()
        verification = database_backup_verification_freshness_check()

    assert backup.healthy is True
    assert verification.healthy is True
    assert "initialization grace period" in backup.message


@override_settings(BACKUP_MAX_AGE_SECONDS=172800)
def test_unavailable_backup_status_fails_during_deployment_grace(tmp_path: Path) -> None:
    (tmp_path / "last-backup").write_text("unavailable")
    recent_deployment = (timezone.now() - timedelta(minutes=5)).isoformat()

    with override_settings(
        BACKUP_STATUS_DIR=str(tmp_path), RELEASE_DEPLOYED_AT=recent_deployment
    ):
        backup = database_backup_freshness_check()

    assert backup.healthy is False
    assert backup.message == "The latest database backup status is unavailable."
