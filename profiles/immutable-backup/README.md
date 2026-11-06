# Immutable backup profile

This default profile provides one application-independent, non-root image for
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

The backup profile is independent of `SCAFFOLD_PROFILE` and enabled by default in
the committed `.scaffold-profile` file:

```make
SCAFFOLD_BACKUP_PROFILE ?= immutable-backup
CI_BACKUP_PROFILES ?= immutable-backup
```

Leave `SCAFFOLD_BACKUP_PROFILE` as `none` when the project does not use the
profile. Set `CI_BACKUP_PROFILES` to an empty value when a derived repository
does not need scaffold CI to exercise the catalog entry.

Bare `make prepare-prod` prepares backup inputs and host storage. `make up-prod`
also builds the backup image, initializes its status storage, and starts the
scheduled worker after the database is healthy. Its first cycle takes a backup
and verifies a restore; subsequent cycles use the configured intervals.
The default local repository is `$(CURDIR)/deploy/backups`; override it with
`BACKUP_LOCAL_PATH`. Local backups alone do not survive loss of the host.

The worker mounts `BACKUP_LOCAL_PATH` at `/backups` for the default encrypted
Restic repository (`BACKUP_REPOSITORY=/backups`). The same capture, validation,
and restore commands work on a laptop or server. Host tooling owns off-host
publication and its credentials; the scaffold does not configure a provider,
mount host transport credentials, or prepare an rclone directory. Off-host
publication is not automated by this workflow.

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

When this profile is enabled, `make prepare-prod` also validates the worker's
required database and repository inputs. Supply `BACKUP_PASSWORD` in the private
production env file or existing environment; recovery requires the original key.
In a terminal, missing backup credentials are prompted without echo and saved to
the private env file. Unattended runs require those values upfront and fail clearly
when absent. Existing supplied credentials are preserved without prompting.
Preparation does not generate a replacement key, initialize a repository, or
authenticate to remote storage. Without the profile, backup inputs are not required.

Preparation creates the declared host directories with the backup service's fixed
UID/GID. Single-consumer directories use mode 700; shared consumers must declare a
common group and receive only the group access their mounts require. Targeted
ownership changes may prompt for `sudo`; unattended runs need those privileges
upfront. Preparation prints each exact, shell-quoted privileged command before
invoking sudo with its standard password prompt.
Nonempty repositories with different owners require an
explicit migration.
Preparation does not recursively change existing backup data ownership.
