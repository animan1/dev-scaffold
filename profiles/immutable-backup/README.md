# Immutable backup profile

This optional profile provides one application-independent, non-root image for
backup and recovery tooling. It combines immutable inputs for:

- PostgreSQL 16.10 Bookworm, supplying version-compatible `pg_dump` and
  `pg_restore` clients;
- Restic 0.19.1, supplying encrypted and versioned repository snapshots; and
- rclone 1.74.4, supplying a transport boundary for a project-selected remote.

The image contains the shared backup/restore operations, but no application
code, credentials, provider configuration or tenant schedule. A downstream
project must not add provider credentials to the image.

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

The backup tool is always available for pre-migration snapshots; selecting the
profile additionally starts its unattended worker. Maintenance and validation use:

```sh
make deps-lock
make setup
make build
make image-check
```

`deps-lock` resolves the version declarations in `deploy/dependencies.json` into
digest pins. `image-check` reports installed versions from the captured image.
See [backup and recovery workflows](../../docs/makefile-workflows.md#4-database-backup-and-recovery)
for manual and periodic restore testing.
