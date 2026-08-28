#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"

export PGPASSWORD="${POSTGRES_PASSWORD}"

readonly PGHOST="${PGHOST:-db}"
readonly BACKUP_HOST="${BACKUP_HOST:-production}"
readonly BACKUP_TAG="${BACKUP_TAG:-postgres}"
readonly BACKUP_FILENAME="${BACKUP_FILENAME:-database.dump}"
readonly BACKUP_STATUS_DIR="${BACKUP_STATUS_DIR:-/backup-status}"
VERIFICATION_DB=""
RESTORE_FILE=""

record_success() {
  local marker="$1"
  record_timestamp "${marker}" "$(date -u +%s)"
}

record_timestamp() {
  local marker="$1"
  local timestamp="$2"
  local temporary_marker
  mkdir -p "${BACKUP_STATUS_DIR}"
  temporary_marker="${BACKUP_STATUS_DIR}/.${marker}.$$"
  printf '%s\n' "${timestamp}" >"${temporary_marker}"
  mv "${temporary_marker}" "${BACKUP_STATUS_DIR}/${marker}"
}

record_unavailable() {
  record_timestamp "$1" "unavailable"
}

record_latest_snapshot() {
  local snapshot_time
  local snapshot_timestamp
  if ! snapshot_time="$(
    restic snapshots --latest 1 --json \
      --host "${BACKUP_HOST}" \
      --tag "${BACKUP_TAG}" \
      | jq -er '.[0].time'
  )"; then
    record_unavailable "last-backup"
    return 1
  fi
  if ! snapshot_timestamp="$(date --date="${snapshot_time}" +%s)"; then
    record_unavailable "last-backup"
    return 1
  fi
  record_timestamp "last-backup" "${snapshot_timestamp}"
}

inspect_repository() {
  if ! open_repository || ! record_latest_snapshot; then
    record_unavailable "last-backup"
    echo "Unable to record the latest recoverable database snapshot." >&2
    return 1
  fi
}

initialize_repository() {
  if open_repository; then
    return
  fi
  echo "No readable backup repository was found; attempting first-time initialization." >&2
  if ! restic init; then
    echo "Backup repository initialization failed." >&2
    echo "If the repository already exists, check RESTIC_PASSWORD," >&2
    echo "RESTIC_REPOSITORY, and the provider credentials." >&2
    exit 1
  fi
}

open_repository() {
  local attempt
  local output=""
  for attempt in 1 2 3; do
    if output="$(restic cat config 2>&1)"; then
      return 0
    fi
    if [[ "${attempt}" != "3" ]]; then
      sleep 2
    fi
  done

  echo "Unable to open the existing backup repository after 3 attempts." >&2
  echo "Check RESTIC_PASSWORD, RESTIC_REPOSITORY," >&2
  echo "and the provider credentials. Restic reported:" >&2
  echo "${output}" >&2
  return 1
}

require_repository() {
  if ! open_repository; then
    exit 1
  fi
}

backup_once() {
  initialize_repository

  pg_dump \
    --host "${PGHOST}" \
    --username "${POSTGRES_USER}" \
    --format custom \
    --no-owner \
    --no-privileges \
    "${POSTGRES_DB}" \
    | restic backup \
        --stdin \
        --stdin-filename "${BACKUP_FILENAME}" \
        --host "${BACKUP_HOST}" \
        --tag "${BACKUP_TAG}"

  restic forget \
    --host "${BACKUP_HOST}" \
    --tag "${BACKUP_TAG}" \
    --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
    --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
    --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" \
    --prune
  restic check
  record_latest_snapshot
}

create_verification_database() {
  local attempt
  local candidate
  local create_output=""
  local suffix
  for attempt in 1 2 3 4 5; do
    suffix="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
    candidate="backup_restore_verify_${suffix}"
    if create_output="$(
      createdb \
        --host "${PGHOST}" \
        --username "${POSTGRES_USER}" \
        "${candidate}" 2>&1
    )"; then
      VERIFICATION_DB="${candidate}"
      return 0
    fi
  done

  echo "Unable to create an isolated verification database after 5 attempts." >&2
  echo "PostgreSQL reported:" >&2
  echo "${create_output}" >&2
  return 1
}

verify_restore() {
  require_repository

  cleanup() {
    if [[ -z "${VERIFICATION_DB}" ]]; then
      return
    fi
    dropdb \
      --host "${PGHOST}" \
      --username "${POSTGRES_USER}" \
      --if-exists \
      "${VERIFICATION_DB}" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT INT TERM

  cleanup
  create_verification_database
  restic dump \
    --host "${BACKUP_HOST}" \
    --tag "${BACKUP_TAG}" \
    latest "${BACKUP_FILENAME}" \
    | pg_restore \
        --host "${PGHOST}" \
        --username "${POSTGRES_USER}" \
        --dbname "${VERIFICATION_DB}" \
        --no-owner \
        --no-privileges \
        --exit-on-error

  psql \
    --host "${PGHOST}" \
    --username "${POSTGRES_USER}" \
    --dbname "${VERIFICATION_DB}" \
    --tuples-only \
    --command "SELECT current_database()" >/dev/null
  record_success "last-restore-verification"
  echo "Backup restored successfully into temporary database ${VERIFICATION_DB}."
  cleanup
  trap - EXIT INT TERM
}

list_snapshots() {
  require_repository
  restic snapshots --host "${BACKUP_HOST}" --tag "${BACKUP_TAG}"
}

restore_database() {
  if [[ "${BACKUP_RESTORE_CONFIRMATION:-}" != "restore-production-database" ]]; then
    echo "Refusing to replace the configured database without confirmation." >&2
    exit 1
  fi
  if [[ "${POSTGRES_DB}" =~ ^(postgres|template0|template1)$ ]]; then
    echo "Refusing to replace reserved PostgreSQL database ${POSTGRES_DB}." >&2
    exit 1
  fi

  require_repository
  cleanup_restore_file() {
    if [[ -n "${RESTORE_FILE}" ]]; then
      rm -f "${RESTORE_FILE}"
    fi
  }
  trap cleanup_restore_file EXIT INT TERM

  RESTORE_FILE="$(mktemp)"
  restic dump \
    --host "${BACKUP_HOST}" \
    --tag "${BACKUP_TAG}" \
    "${BACKUP_RESTORE_SNAPSHOT:-latest}" "${BACKUP_FILENAME}" \
    >"${RESTORE_FILE}"
  pg_restore --list "${RESTORE_FILE}" >/dev/null

  dropdb \
    --host "${PGHOST}" \
    --username "${POSTGRES_USER}" \
    --maintenance-db postgres \
    --force \
    --if-exists \
    "${POSTGRES_DB}"
  createdb \
    --host "${PGHOST}" \
    --username "${POSTGRES_USER}" \
    --maintenance-db postgres \
    "${POSTGRES_DB}"
  pg_restore \
    --host "${PGHOST}" \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    "${RESTORE_FILE}"
  echo "Configured database restored from snapshot ${BACKUP_RESTORE_SNAPSHOT:-latest}."
  cleanup_restore_file
  trap - EXIT INT TERM
}

watch() {
  local backup_interval="${BACKUP_INTERVAL_SECONDS:-86400}"
  local inspection_interval="${BACKUP_INSPECTION_INTERVAL_SECONDS:-900}"
  local next_backup_at=0
  local now
  local retry_interval="${BACKUP_RETRY_INTERVAL_SECONDS:-${inspection_interval}}"
  if ! [[ "${backup_interval}" =~ ^[1-9][0-9]*$ ]]; then
    echo "BACKUP_INTERVAL_SECONDS must be a positive integer." >&2
    return 1
  fi
  if ! [[ "${inspection_interval}" =~ ^[1-9][0-9]*$ ]]; then
    echo "BACKUP_INSPECTION_INTERVAL_SECONDS must be a positive integer." >&2
    return 1
  fi
  if ! [[ "${retry_interval}" =~ ^[1-9][0-9]*$ ]]; then
    echo "BACKUP_RETRY_INTERVAL_SECONDS must be a positive integer." >&2
    return 1
  fi
  if (( retry_interval > backup_interval )); then
    retry_interval="${backup_interval}"
  fi
  while true; do
    now="$(date -u +%s)"
    if (( now >= next_backup_at )); then
      if backup_once && verify_restore; then
        next_backup_at=$((now + backup_interval))
      else
        echo "Scheduled database backup or restore verification failed." >&2
        echo "Retrying in ${retry_interval} seconds." >&2
        next_backup_at=$((now + retry_interval))
      fi
    elif ! inspect_repository; then
      echo "Scheduled backup repository inspection failed." >&2
    fi
    sleep "${inspection_interval}"
  done
}

case "${1:-watch}" in
  once) backup_once ;;
  verify) verify_restore ;;
  inspect) inspect_repository ;;
  snapshots) list_snapshots ;;
  restore) restore_database ;;
  watch) watch ;;
  *)
    echo "Usage: scaffold-backup [once|verify|inspect|snapshots|restore|watch]" >&2
    exit 2
    ;;
esac
