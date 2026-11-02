# Makefile workflows

Proposed workflows for the Makefile redesign, using the command convention in
[the command contract](makefile-design.md). These are design examples,
not instructions for the current implementation. Existing command definitions
remain authoritative; the examples add no implicit build, test, or deploy steps.

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
