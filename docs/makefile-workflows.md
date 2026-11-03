# Makefile workflows

Proposed workflows for the Makefile redesign, using the command convention in
[the command contract](makefile-design.md). These are design examples,
not instructions for the current implementation. Existing command definitions
remain authoritative; the examples add no implicit build, test, or deploy steps.

## 0. First-time installation

Infra prepares the host with Git, Make, Docker with Compose, and the selected
profile's host tooling, then obtains the tenant's deployment checkout. From that
checkout, inspect the selected profiles and prepare their tooling/dependencies:

```sh
make help
make profiles
make setup
```

This preparation is environment-neutral. Use the scaffold's configuration
templates and private environment files, extended with tenant-specific settings,
to supply the intended environment's project identity, ports/ingress, application/database
secrets, and backup storage/keys when enabled. Production also needs durable
`DEPLOYMENT_STATE_DIR` storage. Recovery uses the original identity and secrets.

Continue with workflow 1 for development, workflow 2 for a local production
build, workflow 3 for a published release, or workflow 6 for recovery. These
workflows own image selection, database initialization/migration, and startup;
first-time preparation leaves the application stopped. After downloading a
release, run its `setup` step again for that exact checkout's dependencies.

## 1. Local dev lifecycle

On the development host, with source and development configuration available:

```sh
make up-dev
make verify-dev
make down-dev
```

The application runs from source in `<project>-dev`. Repeat `up-dev` and
`verify-dev` after code changes when needed. `down-dev` retains development data
for the next startup. No production-image build or registry transfer is needed.

Use `make help` for command discovery. To opt into local Git hooks:

```sh
make setup
make hooks
```

To inspect the selected profiles and configured validation matrix:

```sh
make profiles
```

During code changes, run individual source gates as needed:

```sh
make format-check
make lint
make typecheck
make migrations-check
make framework-check
make test
```

Use `make format` for formatting and fixes, or `make check` for the complete gate.

For local cache troubleshooting, use `make clean`.

For an intentional dependency update:

```sh
make deps-lock
make setup
make check
```

Commit the dependency declarations and lockfiles together before building a release.

After an intentional model change:

```sh
make migrations
make check
make migrate-dev
make verify-dev
```

Commit model changes and migration files together before building a release.
For interactive administration of the running development application, use
`make superuser-dev` to create an administrator account.

## 2. Local prod lifecycle

On one host, with the intended source committed and production configuration
available, run in order and stop at the first failure:

```sh
make setup
make check
make build
make image-check
make up-ephemeral
make integration-test-ephemeral
make verify-ephemeral
make rm-ephemeral
make up-prod
make verify-prod
```

The existing prod stays running during build and ephemeral testing. The same
recorded images then run in `<project>-prod`, retaining its configuration and
volumes. Use `make down-prod` to stop it while preserving data; `make up-prod`
starts it again. Neither startup nor restart rebuilds or retests those images.

## 3. GHCR build → prod deploy

On the build host, with the intended source committed:

```sh
make setup
make check
make build
make image-check
make up-ephemeral
make integration-test-ephemeral
make verify-ephemeral
make rm-ephemeral
make publish
```

On the production host, download that published release, then use its prepared
checkout for the same production startup and verification as the local workflow:

```sh
make download
make setup
make up-prod
make verify-prod
```

The host boundary adds publication and download, not a different deployment
path. The downloaded release records the same tested image identities and
revision. Its architecture must match the destination. The destination does not
rebuild, substitute tags, or repeat writable tests against production data.
Stopping and starting prod uses the same commands as workflow 2.

For either production workflow, a failed ephemeral test stops promotion; use
`make rm-ephemeral` for cleanup. A production migration failure stops startup and
does not imply database changes can be reversed. Remote orchestration belongs
to infra; these commands operate on their current host.

## 4. Database backup and recovery

On a host with backup storage and credentials configured:

```sh
make backup-prod
make snapshots-prod
```

The configured backup worker periodically creates a backup and runs the same
restore test as `backup-test-prod` after each successful backup. It records
restore-test freshness for `monitor-check-prod`; monitoring does not perform the
restore. To run the test on demand:

```sh
make backup-test-prod
```

This tests database restorability without stopping application writers. It uses
resources on the existing database server and does not test application behavior
or recovery on a replacement host. Use the ephemeral recovery sequence below
for application-level validation.

Replace `SNAPSHOT_ID` below with an ID from that list. Use an application checkout
compatible with the chosen snapshot. First check recovery with disposable data:

```sh
make restore-ephemeral BACKUP_SNAPSHOT=SNAPSHOT_ID CONFIRM=restore-ephemeral
make up-ephemeral
make verify-ephemeral
make rm-ephemeral
```

For an intentional replacement of the application's database:

```sh
make restore-prod BACKUP_SNAPSHOT=SNAPSHOT_ID CONFIRM=restore-prod
make up-prod
make verify-prod
```

This is database recovery, not release recovery. Restoring a snapshot does not
select an application image or make migrations reversible.

## 5. Rollback after an update

On the deployment host, inspect the recorded update, then restore its previous
release and pre-migration snapshot without copying revision or snapshot IDs:

```sh
make history-prod
make rollback-prod CONFIRM=rollback-prod
```

Rollback uses automatically retained local release artifacts and the recorded
snapshot pair, not the latest published release or scheduled backup. For a
different recovery point, inspect `history-prod` and `snapshots-prod`, then supply
both the full revision and compatible snapshot explicitly:

```sh
make rollback-prod CONFIRM=rollback-prod RELEASE_REVISION=REVISION BACKUP_SNAPSHOT=SNAPSHOT_ID
```

The selected release supplies matching deployment source and recorded images.
Restore stops application writers
and replaces the database with the selected snapshot, discarding subsequent
database writes. Startup uses the normal migration workflow for that revision.
Configuration and volumes retain their identity. This is an explicit database
recovery, not a reversal of migrations. If the current schema and data are known
to be compatible with the previous release, use `download RELEASE_REVISION=REVISION`,
`setup`, `image-check`, `up-prod`, and `verify-prod` for an application-only rollback.

## 6. Disaster recovery on a new host

Infra prepares the replacement host with Docker, Make, a deployment checkout,
the original logical deployment identity, configuration and credentials, backup
repository access and decryption keys, and registry access. The matching release
artifacts must be retained off-host, including locally built releases. Restore
any application-specific non-database persistent data through its own procedure.
Keep traffic and writers on the old host fenced off before cutover.

Select the newest backup once and use its recorded revision, not the latest
published release. Run in order, stopping at the first failure:

```sh
make snapshots-prod SELECT=newest ENV_FILE=.local/recovery.env
. .local/recovery.env
make download RELEASE_REVISION="$RELEASE_REVISION"
make setup
make image-check
make restore-prod BACKUP_SNAPSHOT="$BACKUP_SNAPSHOT" CONFIRM=restore-prod
make up-prod
make verify-prod
```

Snapshot discovery requires neither the lost host nor its deployment history.
Missing release metadata or unavailable matching artifacts stop recovery rather
than selecting an older backup, rebuilding, or guessing a compatible revision.
Restore invokes the normal service-scoped lifecycle commands to stop writers and
start the database (`down-prod-<writer>` and `up-prod-db` for the dummy project).
Database-only startup does not migrate or start the application; `up-prod`
follows only after successful restore. Infra directs traffic to
the replacement after verification. Database writes after the selected snapshot
are not recovered by this workflow.

## Service-level operations

Examples using the dummy project's Compose service names, run independently:

```sh
make restart-prod-backend
make logs-prod-backend
make logs-prod-backup
make down-prod-backup
make up-prod-backup
```

The backup service uses the same selectors as the other services. All examples
operate within `<project>-prod`.

## Operational monitoring

With monitoring configuration and notification destinations configured, explicitly
run a single monitoring pass:

```sh
make monitor-check-prod
```

## Configuration inspection

To inspect the desired configuration before deployment:

```sh
make config-prod
```

Use the generated private JSON file to review service images, mounts, and project
identity. It is inspection output, not another configuration input to maintain.
