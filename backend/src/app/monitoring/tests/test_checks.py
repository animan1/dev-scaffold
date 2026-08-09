from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils import timezone

from app.monitoring.checks import (
    database_backup_freshness_check,
    database_backup_verification_freshness_check,
    database_connectivity_check,
    operational_checks,
)


@pytest.mark.django_db
def test_database_connectivity_is_a_generic_operational_check() -> None:
    result = database_connectivity_check()
    assert result.healthy
    assert result.key == "database-connectivity"


def test_database_connectivity_failure_becomes_a_result() -> None:
    with patch("app.monitoring.checks.connection.ensure_connection", side_effect=OSError):
        result = database_connectivity_check()
    assert not result.healthy
    assert result.message == "The database is unavailable."


def test_backup_checks_are_optional_when_no_status_directory(settings: object) -> None:
    settings.BACKUP_STATUS_DIR = ""  # type: ignore[attr-defined]
    assert database_backup_freshness_check().healthy
    assert database_backup_verification_freshness_check().healthy


@pytest.mark.parametrize(
    ("expected_key", "check"),
    [
        ("database-backup-freshness", database_backup_freshness_check),
        (
            "database-backup-verification-freshness",
            database_backup_verification_freshness_check,
        ),
    ],
)
def test_backup_checks_require_success_markers(
    expected_key: str, check: object, tmp_path: Path, settings: object
) -> None:
    settings.BACKUP_STATUS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    result = check()  # type: ignore[operator]
    assert result.key == expected_key
    assert not result.healthy


def test_backup_check_accepts_a_fresh_marker(tmp_path: Path, settings: object) -> None:
    settings.BACKUP_STATUS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    settings.BACKUP_MAX_AGE_SECONDS = 3600  # type: ignore[attr-defined]
    (tmp_path / "last-backup").write_text(str(int(timezone.now().timestamp())))
    assert database_backup_freshness_check().healthy


def test_backup_check_rejects_stale_and_invalid_markers(tmp_path: Path, settings: object) -> None:
    settings.BACKUP_STATUS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    settings.BACKUP_MAX_AGE_SECONDS = 60  # type: ignore[attr-defined]
    marker = tmp_path / "last-backup"
    marker.write_text(str(int((timezone.now() - timedelta(hours=1)).timestamp())))
    assert "stale" in database_backup_freshness_check().message
    marker.write_text("not-a-time")
    assert "unavailable" in database_backup_freshness_check().message


def test_new_deployment_has_bounded_backup_initialization_grace(
    tmp_path: Path, settings: object
) -> None:
    settings.BACKUP_STATUS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    settings.BACKUP_MAX_AGE_SECONDS = 3600  # type: ignore[attr-defined]
    settings.RELEASE_DEPLOYED_AT = timezone.now().isoformat()  # type: ignore[attr-defined]
    result = database_backup_freshness_check()
    assert result.healthy
    assert "initialization grace period" in result.message


def test_naive_or_future_deployment_time_does_not_extend_grace(
    tmp_path: Path, settings: object
) -> None:
    settings.BACKUP_STATUS_DIR = str(tmp_path)  # type: ignore[attr-defined]
    settings.BACKUP_MAX_AGE_SECONDS = 3600  # type: ignore[attr-defined]
    settings.RELEASE_DEPLOYED_AT = timezone.now().replace(tzinfo=None).isoformat()  # type: ignore[attr-defined]
    assert not database_backup_freshness_check().healthy
    settings.RELEASE_DEPLOYED_AT = (timezone.now() + timedelta(minutes=1)).isoformat()  # type: ignore[attr-defined]
    assert not database_backup_freshness_check().healthy


@pytest.mark.django_db
def test_default_registry_contains_only_generic_checks(settings: object) -> None:
    settings.BACKUP_STATUS_DIR = ""  # type: ignore[attr-defined]
    assert [result.key for result in operational_checks()] == [
        "database-connectivity",
        "database-backup-freshness",
        "database-backup-verification-freshness",
    ]
