from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def _script() -> str:
    return (_repository_root() / "profiles/immutable-backup/backup.sh").read_text()


def _operation(start: str, end: str) -> str:
    return _script().split(start, 1)[1].split(end, 1)[0]


def test_backup_script_has_valid_bash_syntax() -> None:
    subprocess.run(
        ["bash", "-n", "profiles/immutable-backup/backup.sh"],
        cwd=_repository_root(),
        check=True,
    )


def test_backup_is_custom_format_and_validated_before_success() -> None:
    backup = _operation("backup_once()", "verify_restore()")

    assert "pg_dump" in backup
    assert "--format custom" in backup
    assert backup.index("restic check") < backup.index("record_latest_snapshot")


def test_verification_uses_and_removes_an_isolated_database() -> None:
    verification = _operation("verify_restore()", "list_snapshots()")

    assert 'VERIFICATION_DB="backup_restore_verify_$$"' in verification
    assert verification.index("createdb") < verification.index("pg_restore")
    assert verification.index("pg_restore") < verification.index(
        'command "SELECT current_database()"'
    )
    assert verification.index('record_success "last-restore-verification"') < (
        verification.rindex("cleanup")
    )
    assert "dropdb" in verification
    assert "trap cleanup EXIT INT TERM" in verification


def test_restore_validates_download_before_replacing_database() -> None:
    restore = _operation("restore_database()", "watch()")

    assert restore.index("pg_restore --list") < restore.index("dropdb")
    assert "BACKUP_RESTORE_CONFIRMATION" in restore
    assert "restore-production-database" in restore
    assert "postgres|template0|template1" in restore


def test_non_backup_operations_never_initialize_repository() -> None:
    for start, end in (
        ("verify_restore()", "list_snapshots()"),
        ("list_snapshots()", "restore_database()"),
        ("restore_database()", "watch()"),
    ):
        operation = _operation(start, end)
        assert "require_repository" in operation
        assert "initialize_repository" not in operation

    inspection = _operation("inspect_repository()", "initialize_repository()")
    assert "open_repository" in inspection
    assert "initialize_repository" not in inspection


def test_repository_access_is_bounded_and_actionable() -> None:
    opening = _operation("open_repository()", "require_repository()")

    assert "for attempt in 1 2 3" in opening
    assert "after 3 attempts" in opening
    assert "RESTIC_PASSWORD" in opening
    assert "RESTIC_REPOSITORY" in opening
    assert "provider credentials" in opening


def test_backup_freshness_comes_from_latest_recoverable_snapshot() -> None:
    freshness = _operation("record_latest_snapshot()", "inspect_repository()")

    assert "restic snapshots --latest 1 --json" in freshness
    assert '--host "${BACKUP_HOST}"' in freshness
    assert '--tag "${BACKUP_TAG}"' in freshness
    assert "date --date" in freshness
    assert 'record_timestamp "last-backup" "${snapshot_timestamp}"' in freshness
    assert 'record_unavailable "last-backup"' in freshness


def test_worker_schedules_backup_verification_and_repository_inspection() -> None:
    worker = _operation("watch()", 'case "${1:-watch}"')

    assert "backup_once" in worker
    assert "verify_restore" in worker
    assert "inspect_repository" in worker
    assert "BACKUP_INTERVAL_SECONDS" in worker
    assert "BACKUP_INSPECTION_INTERVAL_SECONDS" in worker
