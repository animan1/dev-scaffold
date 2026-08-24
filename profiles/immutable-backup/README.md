# Immutable backup profile

This optional profile provides one application-independent, non-root image for
backup and recovery tooling. It combines immutable inputs for:

- PostgreSQL 16.10 Bookworm, supplying version-compatible `pg_dump` and
  `pg_restore` clients;
- Restic 0.19.1, supplying encrypted and versioned repository snapshots; and
- rclone 1.74.4, supplying a transport boundary for a project-selected remote.

The image contains no application code, credentials, provider configuration,
backup schedule, or backup/restore operation. Those capabilities are separate
reviewable slices. A downstream project must not add provider credentials to
the image.

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
verification. The component-maintenance and focused exercise commands are:

```sh
make backup-images-digests
make backup-image-versions
make verify-backup-image
```

`backup-images-digests` pulls the declared version tags and prints current
registry digests for dependency review. Updating a component requires changing
the versioned source and immutable digest declarations together.
