from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from organizations.models import Organization

from app.integrations.models import GoogleCalendarConnection
from app.integrations.services import (
    GoogleCalendarConfigurationError,
    duplicate_cuplr_google_calendars,
    google_calendar_readiness,
)

ACTIVE_USER_ORGANIZATION_CHECK = "active-user-organization-count"
GOOGLE_CALENDAR_READINESS_CHECK = "google-calendar-installation-readiness"
GOOGLE_CALENDAR_DUPLICATE_CHECK = "google-calendar-duplicates"
DATABASE_BACKUP_FRESHNESS_CHECK = "database-backup-freshness"
DATABASE_BACKUP_VERIFICATION_FRESHNESS_CHECK = "database-backup-verification-freshness"


@dataclass(frozen=True)
class OperationalCheckResult:
    key: str
    label: str
    healthy: bool
    message: str


def active_user_organization_check() -> OperationalCheckResult:
    invalid_users: list[str] = []
    for user in User.objects.filter(is_active=True).order_by("username"):
        organization_count = Organization.active.get_for_user(user).count()
        if organization_count != 1:
            invalid_users.append(f"{user.username} ({organization_count})")

    if invalid_users:
        return OperationalCheckResult(
            key=ACTIVE_USER_ORGANIZATION_CHECK,
            label="Active user organization count",
            healthy=False,
            message=(
                "Active users must belong to exactly one active organization. "
                f"Unsupported users: {', '.join(invalid_users)}."
            ),
        )
    return OperationalCheckResult(
        key=ACTIVE_USER_ORGANIZATION_CHECK,
        label="Active user organization count",
        healthy=True,
        message="Every active user belongs to exactly one active organization.",
    )


def google_calendar_installation_readiness_check() -> OperationalCheckResult:
    readiness = google_calendar_readiness()
    return OperationalCheckResult(
        key=GOOGLE_CALENDAR_READINESS_CHECK,
        label="Google Calendar installation readiness",
        healthy=readiness.ready,
        message=readiness.operator_message,
    )


def google_calendar_duplicate_check() -> OperationalCheckResult:
    if not google_calendar_readiness().ready:
        return OperationalCheckResult(
            key=GOOGLE_CALENDAR_DUPLICATE_CHECK,
            label="Google Calendar duplicates",
            healthy=True,
            message="Duplicate inspection is waiting for Google Calendar configuration.",
        )

    issues: list[str] = []
    connections = (
        GoogleCalendarConnection.objects.exclude(refresh_token__isnull=True)
        .select_related("organization")
        .order_by("organization__name")
    )
    for connection in connections:
        try:
            duplicates = duplicate_cuplr_google_calendars(connection)
        except GoogleCalendarConfigurationError as error:
            issue = f"{connection.organization.name}: inspection failed ({error})"
            issues.append(issue)
            continue
        if duplicates:
            calendar_ids = ", ".join(calendar.id for calendar in duplicates)
            issues.append(f"{connection.organization.name}: {calendar_ids}")

    if issues:
        return OperationalCheckResult(
            key=GOOGLE_CALENDAR_DUPLICATE_CHECK,
            label="Google Calendar duplicates",
            healthy=False,
            message=f"Google Calendar needs reconciliation. {'; '.join(issues)}.",
        )
    return OperationalCheckResult(
        key=GOOGLE_CALENDAR_DUPLICATE_CHECK,
        label="Google Calendar duplicates",
        healthy=True,
        message="No organizations have duplicate Cuplr-managed Google calendars.",
    )


def database_backup_freshness_check() -> OperationalCheckResult:
    return _backup_marker_check(
        key=DATABASE_BACKUP_FRESHNESS_CHECK,
        label="Database backup freshness",
        marker_name="last-backup",
        success_description="database backup",
    )


def database_backup_verification_freshness_check() -> OperationalCheckResult:
    return _backup_marker_check(
        key=DATABASE_BACKUP_VERIFICATION_FRESHNESS_CHECK,
        label="Database backup restore verification freshness",
        marker_name="last-restore-verification",
        success_description="database backup restore verification",
    )


def _backup_marker_check(
    *, key: str, label: str, marker_name: str, success_description: str
) -> OperationalCheckResult:
    status_directory = str(settings.BACKUP_STATUS_DIR)
    if not status_directory:
        return OperationalCheckResult(
            key=key,
            label=label,
            healthy=True,
            message="Backup freshness monitoring is not configured in this environment.",
        )

    now = timezone.now()
    max_age_seconds = int(settings.BACKUP_MAX_AGE_SECONDS)
    marker_path = Path(status_directory) / marker_name
    try:
        marker = marker_path.read_text().strip()
    except OSError:
        if _deployment_is_within_backup_grace(now, max_age_seconds):
            return OperationalCheckResult(
                key=key,
                label=label,
                healthy=True,
                message=(
                    f"No successful {success_description} has been recorded yet; "
                    "the deployment is within its initialization grace period."
                ),
            )
        return OperationalCheckResult(
            key=key,
            label=label,
            healthy=False,
            message=f"No successful {success_description} has been recorded.",
        )

    try:
        timestamp = int(marker)
        current_timezone = timezone.get_current_timezone()
        succeeded_at = datetime.fromtimestamp(timestamp, tz=current_timezone)
    except (ValueError, OverflowError):
        return OperationalCheckResult(
            key=key,
            label=label,
            healthy=False,
            message=f"The latest {success_description} status is unavailable.",
        )

    age_seconds = (now - succeeded_at).total_seconds()
    if age_seconds > max_age_seconds:
        return OperationalCheckResult(
            key=key,
            label=label,
            healthy=False,
            message=(
                f"The last successful {success_description} was at "
                f"{succeeded_at.isoformat()} and is stale."
            ),
        )
    return OperationalCheckResult(
        key=key,
        label=label,
        healthy=True,
        message=f"The latest successful {success_description} is current.",
    )


def _deployment_is_within_backup_grace(now: datetime, max_age_seconds: int) -> bool:
    try:
        deployed_at = datetime.fromisoformat(str(settings.RELEASE_DEPLOYED_AT))
    except ValueError:
        return False
    if timezone.is_naive(deployed_at):
        return False
    deployment_age = now - deployed_at
    return timedelta(0) <= deployment_age <= timedelta(seconds=max_age_seconds)


def operational_checks() -> tuple[OperationalCheckResult, ...]:
    return (
        active_user_organization_check(),
        google_calendar_installation_readiness_check(),
        google_calendar_duplicate_check(),
        database_backup_freshness_check(),
        database_backup_verification_freshness_check(),
    )
