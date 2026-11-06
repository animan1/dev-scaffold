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
Preparation asks for an absolute `BACKUP_LOCAL_PATH` when absent and saves it in
the private production env file. Press Enter to accept the suggested
`~/.local/share/<checkout-name>/backups` directory outside the checkout.
The directory prompt supports Tab completion (including paths with spaces) and `~`.
Values already supplied there, in the environment, or as Make arguments are
honored; unattended preparation requires the path upfront.
Local backups alone do not survive loss of the host.

For persistent unattended inputs, add these non-secret settings to
`deploy/.env.prod` (or the file selected by `PROD_ENV_FILE`):

```dotenv
BACKUP_LOCAL_PATH="/absolute/host/backups/cuplr"
BACKUP_GROUP="cuplr-backup"
```

Alternatively supply Make arguments, for example
`make prepare-prod BACKUP_LOCAL_PATH=/absolute/host/backups/cuplr BACKUP_GROUP=cuplr-backup`,
or export those variables in the shell before running Make.

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

Preparation preserves the host directory's owner. The worker keeps its dedicated
UID and uses a namespaced host group, `<Compose-project>-backup` by default
(override with `BACKUP_GROUP`). Separate tenants get separate groups and GIDs;
the worker uses the group's resolved GID, saved as `BACKUP_RUNTIME_GID`.
Preparation creates a missing group and adds the operator when necessary using
displayed administrator commands (`dseditgroup` on macOS, `groupadd`/`usermod` on
Linux). Open a new login session after membership changes. Once membership is
active, routine preparation of an operator-owned directory needs no sudo.
Mode 2770 permits operator/worker access and excludes other users; additional
consuming services must share that tenant group.
New local Restic repositories enable [group access](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#group-accessible-repositories)
for subsequent backup contents as well. Existing repositories are not recursively
changed. Targeted permission changes on another owner's directory may require
`sudo`; preparation prints each exact, shell-quoted privileged command before
invoking sudo with its standard password prompt.
Nonempty repositories with a different group require an explicit migration.
Preparation does not recursively change existing backup data ownership.
