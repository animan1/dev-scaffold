from __future__ import annotations

import os
import re
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


def _make(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "--dry-run", *arguments],
        cwd=_repository_root(),
        check=check,
        capture_output=True,
        text=True,
    )


def _restore(*, database: str, confirmation: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "BACKUP_RESTORE_CONFIRMATION": confirmation,
        "POSTGRES_DB": database,
        "POSTGRES_PASSWORD": "test-only",
        "POSTGRES_USER": "test-only",
        "RESTIC_PASSWORD": "test-only",
        "RESTIC_REPOSITORY": "test-only",
    }
    return subprocess.run(
        ["bash", "profiles/immutable-backup/backup.sh", "restore"],
        cwd=_repository_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _fake_backup_commands(directory: Path) -> None:
    commands = {
        "createdb": """#!/bin/sh
if [ -n "${CREATE_ATTEMPT_FILE:-}" ]; then
  count="$(cat "${CREATE_ATTEMPT_FILE}" 2>/dev/null || printf '0')"
  count="$((count + 1))"
  printf '%s\n' "${count}" >"${CREATE_ATTEMPT_FILE}"
  if [ "${count}" = "1" ]; then
    echo 'database already exists' >&2
    exit 1
  fi
fi
""",
        "dropdb": "#!/bin/sh\nexit 0\n",
        "pg_restore": "#!/bin/sh\ncat >/dev/null\n",
        "psql": "#!/bin/sh\nexit 0\n",
        "restic": """#!/bin/sh
case "$1" in
  cat) exit 0 ;;
  dump) printf 'fake custom dump' ;;
  *) exit 0 ;;
esac
""",
    }
    directory.mkdir()
    for name, contents in commands.items():
        command = directory / name
        command.write_text(contents)
        command.chmod(0o755)


def _verification_environment(fake_bin: Path, status: Path) -> dict[str, str]:
    return os.environ | {
        "BACKUP_STATUS_DIR": str(status),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "POSTGRES_DB": "app",
        "POSTGRES_PASSWORD": "test-only",
        "POSTGRES_USER": "test-only",
        "RESTIC_PASSWORD": "test-only",
        "RESTIC_REPOSITORY": "test-only",
    }


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
    creation = _operation("create_verification_database()", "verify_restore()")
    verification = _operation("verify_restore()", "list_snapshots()")

    assert "/dev/urandom" in creation
    assert 'candidate="backup_restore_verify_${suffix}"' in creation
    assert "for attempt in 1 2 3 4 5" in creation
    assert creation.index("createdb") < creation.index('VERIFICATION_DB="${candidate}"')
    assert "$$" not in creation
    assert verification.index("create_verification_database") < verification.index("pg_restore")
    assert verification.index("pg_restore") < verification.index(
        'command "SELECT current_database()"'
    )
    assert verification.index('record_success "last-restore-verification"') < (
        verification.rindex("cleanup")
    )
    assert "dropdb" in verification
    assert "trap cleanup EXIT INT TERM" in verification


def test_concurrent_verifications_use_distinct_database_safe_names(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _fake_backup_commands(fake_bin)
    processes = [
        subprocess.Popen(
            ["bash", "profiles/immutable-backup/backup.sh", "verify"],
            cwd=_repository_root(),
            env=_verification_environment(fake_bin, tmp_path / f"status-{index}"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]
    names = []
    for process, (stdout, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr
        match = re.search(r"backup_restore_verify_[0-9a-f]{24}", stdout)
        assert match is not None
        names.append(match.group())

    assert len(set(names)) == 2


def test_verification_database_creation_retries_without_cleaning_collision(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    attempts = tmp_path / "attempts"
    _fake_backup_commands(fake_bin)
    environment = _verification_environment(fake_bin, tmp_path / "status")
    environment["CREATE_ATTEMPT_FILE"] = str(attempts)

    result = subprocess.run(
        ["bash", "profiles/immutable-backup/backup.sh", "verify"],
        cwd=_repository_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert attempts.read_text().strip() == "2"


def test_restore_validates_download_before_replacing_database() -> None:
    restore = _operation("restore_database()", "watch()")

    assert restore.index("pg_restore --list") < restore.index("dropdb")
    assert "BACKUP_RESTORE_CONFIRMATION" in restore
    assert "restore-production-database" in restore
    assert "postgres|template0|template1" in restore


def test_restore_requires_exact_confirmation_and_rejects_reserved_databases() -> None:
    refused = _restore(database="app", confirmation="restore-prod")
    reserved = _restore(database="template1", confirmation="restore-production-database")

    assert refused.returncode != 0
    assert "without confirmation" in refused.stderr
    assert reserved.returncode != 0
    assert "reserved PostgreSQL database template1" in reserved.stderr


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


def test_active_make_targets_dispatch_through_selected_backup_container() -> None:
    for primary_profile, primary_compose in (
        ("react-vite", "deploy/docker-compose.prod.yml"),
        ("server-rendered-django", "profiles/server-rendered-django/release.compose.yml"),
    ):
        common = (
            f"SCAFFOLD_PROFILE={primary_profile}",
            "SCAFFOLD_BACKUP_PROFILE=immutable-backup",
        )
        backup = _make(*common, "backup-prod").stdout
        verification = _make(*common, "verify-backup-prod").stdout
        snapshots = _make(*common, "snapshots-prod").stdout

        for output in (backup, verification, snapshots):
            assert primary_compose in output
            assert "profiles/immutable-backup/compose.yml" in output
            assert "run --rm --no-deps backup" in output
        assert "backup once" in backup
        assert "backup verify" in verification
        assert "backup snapshots" in snapshots


def test_restore_stops_configured_writers_before_destructive_container() -> None:
    output = _make(
        "SCAFFOLD_BACKUP_PROFILE=immutable-backup",
        "restore-prod",
        "CONFIRM=restore-prod",
        "BACKUP_WRITER_SERVICES=writer worker backup",
    ).stdout

    assert output.index("stop writer worker backup") < output.index("backup restore")
    assert "BACKUP_RESTORE_CONFIRMATION=restore-production-database" in output


def test_backup_configuration_boundaries_remain_project_owned() -> None:
    compose = (_repository_root() / "profiles/immutable-backup/compose.yml").read_text()

    for variable in (
        "BACKUP_DATABASE_HOST",
        "BACKUP_FILENAME",
        "BACKUP_HOST",
        "BACKUP_KEEP_DAILY",
        "BACKUP_KEEP_MONTHLY",
        "BACKUP_KEEP_WEEKLY",
        "BACKUP_LOCAL_PATH",
        "BACKUP_PASSWORD",
        "BACKUP_REPOSITORY",
        "BACKUP_RUNTIME_GID",
        "BACKUP_RUNTIME_UID",
        "BACKUP_RCLONE_CONFIG",
        "BACKUP_STATUS_DIR",
        "BACKUP_TAG",
        "POSTGRES_DB",
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
    ):
        assert "${" + variable in compose


def test_routine_teardown_preserves_all_persistent_volume_classes() -> None:
    react_down = _make(
        "SCAFFOLD_PROFILE=react-vite",
        "SCAFFOLD_BACKUP_PROFILE=immutable-backup",
        "down-prod",
    ).stdout
    server_down = _make(
        "SCAFFOLD_PROFILE=server-rendered-django",
        "SCAFFOLD_BACKUP_PROFILE=immutable-backup",
        "down",
    ).stdout
    react_compose = (_repository_root() / "deploy/docker-compose.prod.yml").read_text()
    server_compose = (
        _repository_root() / "profiles/server-rendered-django/release.compose.yml"
    ).read_text()
    backup_compose = (_repository_root() / "profiles/immutable-backup/compose.yml").read_text()

    assert "down -v" not in react_down
    assert "down -v" not in server_down
    assert "pgdata:" in react_compose
    assert "postgres_data:" in server_compose
    assert "staticfiles:" in react_compose
    assert "media:" in server_compose
    assert "backup_status:" in backup_compose


def test_non_root_storage_is_prepared_without_initializing_restic() -> None:
    compose = (_repository_root() / "profiles/immutable-backup/compose.yml").read_text()
    initialization = compose.split("  backup-init:", 1)[1].split("  backup:", 1)[0]
    snapshots = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "snapshots-prod").stdout

    assert 'user: "0:0"' in initialization
    assert "install -d" in initialization
    assert "restic init" not in initialization
    assert snapshots.index("backup-init") < snapshots.index("backup snapshots")


def test_make_exercises_real_backup_and_isolated_restore_entrypoints() -> None:
    exercise = _make("SCAFFOLD_BACKUP_PROFILE=immutable-backup", "exercise-backup-profile").stdout

    assert ".tmp/immutable-backup-exercise" in exercise
    assert "exercise.compose.yml" in exercise
    assert exercise.index("backup once") < exercise.index("backup verify")
    assert exercise.index("backup verify") < exercise.index("backup snapshots")
    assert "/backup-status/last-backup" in exercise
    assert "/backup-status/last-restore-verification" in exercise
    assert "backup_restore_verify_%" in exercise
    assert "down -v --remove-orphans" in exercise


def test_worker_uses_bounded_retry_interval_after_failure() -> None:
    worker = _operation("watch()", 'case "${1:-watch}"')
    success = worker.split("if backup_once && verify_restore; then", 1)[1].split("else", 1)[0]
    failure = worker.split("else", 1)[1].split("\n      fi", 1)[0]

    assert "BACKUP_RETRY_INTERVAL_SECONDS:-${inspection_interval}" in worker
    assert 'retry_interval="${backup_interval}"' in worker
    assert "now + backup_interval" in success
    assert "now + retry_interval" in failure
    assert 'sleep "${inspection_interval}"' in worker
