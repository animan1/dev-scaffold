# Immutable backup profile

This optional profile provides one application-independent, non-root image for
backup and recovery tooling. It combines immutable inputs for:

- PostgreSQL 16.10 Bookworm, supplying version-compatible `pg_dump` and
  `pg_restore` clients;
- Restic 0.19.1, supplying encrypted and versioned repository snapshots; and
- rclone 1.74.4, supplying a transport boundary for a project-selected remote.

The image contains no application code, credentials, or provider
configuration. Its application-neutral entrypoint implements PostgreSQL
backup, repository inspection, isolated restore verification, snapshot
listing, guarded database restoration, and a scheduled worker loop. A
downstream project must not add provider credentials to the image.

## Select the profile

The backup profile is independent of `SCAFFOLD_PROFILE`. Enable it in the
committed `.scaffold-profile` file:

```make
SCAFFOLD_BACKUP_PROFILE ?= immutable-backup
CI_BACKUP_PROFILES ?= immutable-backup
```

Leave `SCAFFOLD_BACKUP_PROFILE` as `none` when the project does not use the
profile. Set `CI_BACKUP_PROFILES` to an empty value when a derived repository
does not need scaffold CI to exercise the catalog entry.

When selected, the normal `make verify` contract includes exact backup-image
and Compose-overlay verification. The component-maintenance and focused
exercise commands are:

```sh
make backup-images-digests
make backup-image-versions
make verify-backup-image
make verify-backup-compose
```

`backup-images-digests` pulls the declared version tags and prints current
registry digests for dependency review. Updating a component requires changing
the versioned source and immutable digest declarations together.

## Adopt the production operations

Keep the entire `profiles/immutable-backup/` directory and the optional-profile
include at the end of the scaffold `Makefile`. The overlay composes with the
production topology selected by the primary profile:

- `react-vite` uses `deploy/docker-compose.prod.yml` and the configurable
  `COMPOSE_PROJECT_NAME`;
- `server-rendered-django` uses its release Compose files and the configurable
  `RELEASE_COMPOSE_PROJECT`.

The selected production topology must expose a PostgreSQL service. Its Compose
service name defaults to `db`; override `BACKUP_DATABASE_SERVICE` for Make and
`BACKUP_DATABASE_HOST` for PostgreSQL network access when a project uses a
different name. Set `BACKUP_WRITER_SERVICES` to every application or worker
service that can write to the configured database so guarded restoration can
stop them first.

Supply these values through the selected production environment and secret
delivery mechanism; do not commit them:

```dotenv
POSTGRES_DB=app
POSTGRES_USER=app
POSTGRES_PASSWORD=replace-me
BACKUP_PASSWORD=replace-with-a-long-random-restic-password
BACKUP_REPOSITORY=/backups
```

`BACKUP_REPOSITORY` may instead be any Restic-supported repository URL. For an
rclone-backed repository, place the project-owned configuration in the ignored
`deploy/rclone/` directory or override `BACKUP_RCLONE_CONFIG_DIR` and
`BACKUP_RCLONE_CONFIG`. The default local repository is the ignored
`deploy/backups/` directory. Use durable off-host storage before treating the
profile as a production disaster-recovery system.

The following boundaries are configurable without changing the generic script:

- snapshot identity: `BACKUP_HOST`, `BACKUP_TAG`, and `BACKUP_FILENAME`;
- retention: `BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`, and
  `BACKUP_KEEP_MONTHLY`;
- scheduling: `BACKUP_INTERVAL_SECONDS` and
  `BACKUP_INSPECTION_INTERVAL_SECONDS`;
- storage and status: `BACKUP_LOCAL_PATH`, `BACKUP_REPOSITORY`,
  `BACKUP_STATUS_DIR`, and `BACKUP_RCLONE_CONFIG_DIR`;
- container identity: `BACKUP_RUNTIME_UID` and `BACKUP_RUNTIME_GID` (both
  default to the image-owned non-root identity `10001`);
- Compose and services: the primary profile's project-name variable,
  `BACKUP_DATABASE_SERVICE`, `BACKUP_DATABASE_HOST`, `BACKUP_SERVICE`, and
  `BACKUP_WRITER_SERVICES`.

## Operate and exercise the profile

Start the selected production database first or let the Make targets do so.
The host needs Docker and Make only; PostgreSQL, Restic, rclone, and Python all
run in containers.

Each operation first runs a short root initialization container that grants the
configured non-root runtime identity access to the repository and status mount.
It does not initialize Restic or change provider credentials. If an rclone
configuration is owner-readable only, set the runtime UID/GID to an identity
that can read it or grant the configured container identity read access.

```sh
make backup-prod
make verify-backup-prod
make snapshots-prod
make inspect-backup-prod
```

`backup-prod` is the only operation allowed to initialize a missing Restic
repository. It streams `pg_dump --format custom` into encrypted Restic storage,
applies retention with pruning, runs `restic check`, and only then records the
latest recoverable snapshot time. `verify-backup-prod` restores the latest
matching snapshot into a temporary PostgreSQL database, queries it, removes it,
and records `last-restore-verification`. `snapshots-prod`, restore verification,
and repository inspection fail with recovery guidance instead of creating a
missing repository.

Start the restartable scheduled worker with:

```sh
make up-backup-prod
make logs-backup-prod
```

The worker performs backup plus isolated restore verification at the backup
interval. Between backups it refreshes `last-backup` from the newest snapshot
that is still present in Restic. Repository failures record `unavailable`, so a
stale success marker cannot hide deleted or unreadable recovery points. The
project-scoped `backup_status` volume preserves both status markers during
routine teardown.

Database restoration is destructive and requires the exact confirmation:

```sh
make snapshots-prod
make restore-prod CONFIRM=restore-prod BACKUP_SNAPSHOT=<snapshot-id>
```

The restore operation downloads the selected dump, validates it with
`pg_restore --list`, and only then drops the configured database. It refuses to
replace `postgres`, `template0`, or `template1`. The generic target deliberately
does not run application migrations, restart writers, or smoke-test a
deployment; after it succeeds, run the consuming project's documented
migration, startup, and validation procedure.

Routine `make down-prod` or profile teardown must remain volume-preserving.
Deleting PostgreSQL, application, repository, or `backup_status` storage is a
separate project-owned destructive recovery operation.

## Downstream extension boundary

This profile backs up one PostgreSQL custom-format dump. It does not capture
Django/Wagtail uploaded media or create a combined database/media recovery
point. Projects that need media recovery should extend the Compose worker and
prove coordinated backup and restore semantics downstream; only reusable,
application-neutral behavior belongs back in this profile.
