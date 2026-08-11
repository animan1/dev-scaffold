from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

DATABASE_CONNECTIVITY_CHECK = "database-connectivity"
DATABASE_BACKUP_FRESHNESS_CHECK = "database-backup-freshness"
DATABASE_BACKUP_VERIFICATION_FRESHNESS_CHECK = "database-backup-verification-freshness"


@dataclass(frozen=True)
class OperationalCheckResult:
    key: str
    label: str
    healthy: bool
    message: str


def database_connectivity_check() -> OperationalCheckResult:
    try:
        connection.ensure_connection()
        healthy = connection.is_usable()
    except Exception:  # noqa: BLE001 - a health check must turn database errors into a result
        healthy = False
    return OperationalCheckResult(
        key=DATABASE_CONNECTIVITY_CHECK,
        label="Database connectivity",
        healthy=healthy,
        message="The database is reachable." if healthy else "The database is unavailable.",
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
    try:
        marker = (Path(status_directory) / marker_name).read_text().strip()
    except OSError:
        if _deployment_is_within_backup_grace(now, max_age_seconds):
            return _initialization_grace_result(key, label, success_description)
        return OperationalCheckResult(
            key=key,
            label=label,
            healthy=False,
            message=f"No successful {success_description} has been recorded.",
        )

    try:
        current_timezone = timezone.get_current_timezone()
        succeeded_at = datetime.fromtimestamp(int(marker), tz=current_timezone)
    except (ValueError, OverflowError):
        if _deployment_is_within_backup_grace(now, max_age_seconds):
            return _initialization_grace_result(key, label, success_description)
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


def _initialization_grace_result(
    key: str, label: str, success_description: str
) -> OperationalCheckResult:
    return OperationalCheckResult(
        key=key,
        label=label,
        healthy=True,
        message=(
            f"No successful {success_description} has been recorded yet; "
            "the deployment is within its initialization grace period."
        ),
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
        database_connectivity_check(),
        database_backup_freshness_check(),
        database_backup_verification_freshness_check(),
    )
