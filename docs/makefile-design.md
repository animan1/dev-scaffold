# Makefile command contract

Proposed contract for the Makefile redesign. This document does not change
existing targets. Implement the agreed contract in subsequent PRs.

## Command names

Targets use `<command>[-<env>[-<container>]]`. The table lists supported forms.
For example, `make up-prod` deploys production on the current host;
`make shell-prod` opens a shell there. `make prod-shell` violates the convention.

Environment-neutral commands, such as `make help` and `make build`, have no suffix.
An environment-specific command must name its environment explicitly; bare
`make up` is not a canonical command under this contract.

Use Compose service names, such as `backend` or `backup`, for `<container>`.
A container selector narrows the command to that service. Container-scoped
lifecycle commands must not start, stop, or recreate other persistent services;
required dependencies must already be available.
Unknown service selectors must fail before service changes.

## Command types

- Setup: prepare local tooling and dependencies.
- Build: produce artifacts.
- Artifact transfer: publish or download recorded release artifacts.
- Lifecycle: start, update (including deployment), stop, or remove an environment.
- Data administration: explicitly manage application data and schema.
- Validation: test source, artifacts, or application behavior.
- Source editing: explicitly update source or lockfiles.
- Cleanup: remove regenerable local artifacts.
- Access and inspection: interact with an existing environment.

## Command results

This table defines command behavior in every supported environment. Examples
in [Makefile workflows](makefile-workflows.md) illustrate composition and do not add requirements.
Subcommands are listed as **Invokes:** at the end of the result.

| Command | Result | Type |
| --- | --- | --- |
| `setup` | Prepare Docker-based tooling and dependencies for the selected application's components from committed lockfiles; no host-native application tooling, lockfile edits, application startup, or deployment-image build | Setup |
| `prepare-<env>` | Prepare private configuration files and host directories from scaffold templates and tenant extensions with ownership and permissions for their consumers; generate missing local secrets and validate required external credentials and ingress inputs. Preserve existing configuration and credentials; recovery requires the original secrets. No service startup, image build/pull, migration, or application-data changes | Setup |
| `hooks` | Install the repository's configured Git hooks for the current checkout, using `check` for pre-commit validation; no hook execution, lockfile edits, or service startup | Setup |
| `build` | Immutable images of the committed checkout for the host architecture, with revision and image identities recorded; shared across environments, with no application startup | Build |
| `publish` | Upload the recorded build's images, committed deployment source, release manifest, and successful validation evidence; preserve their revision and immutable image identities | Artifact transfer |
| `download` | Obtain the current checkout revision's recorded images, manifest, evidence, and deployment source from retained local artifacts or publication; select its recorded image manifest and validate completeness, integrity, and architecture, without changing Git HEAD, running containers or application data. Reuse validated local artifacts before accessing publication. With `RELEASE_REVISION`, require that exact committed revision instead; fail if unavailable, without rebuilding or substituting another release | Artifact transfer |
| `download-latest` | Resolve the newest successful main release once, check out that release's commit, then obtain that exact release; fail if its required artifacts are unavailable, without selecting an older release. **Invokes:** `download RELEASE_REVISION=<resolved revision>` | Artifact transfer |
| `up-<env>[-<container>]` | Selected services running, matching the checked-out revision; reject a mismatch between the selected image manifest and checked-out revision before service changes. Retain configuration and volumes, and apply required migrations before application startup, without building or testing. Without a container selector, start all configured services and initialize dependencies. Migration failure stops startup; database changes are not automatically reversed. Before migrations for a release-changing update, atomically persist the previous release and the snapshot ID returned by its backup in an owner-only, machine-readable record under `DEPLOYMENT_STATE_DIR/<project>/<env>` in configured host storage outside the checkout and environment containers/volumes. Failure to persist it prevents the update. Records survive failed updates and checkout changes; same-revision restarts do not replace the rollback point. Automatically retain the previous release's source, manifest, evidence and images locally, and protect the paired snapshot from retention pruning while it is the rollback point. **Invokes:** `migrate-<env>` (only when starting application services) | Lifecycle |
| `restart-<env>[-<container>]` | Stop and start selected services using normal startup behavior, preserving configuration and data; stop on failure. **Invokes:** `down-<env>[-<container>]`, then `up-<env>[-<container>]` with the same selectors | Lifecycle |
| `rollback-<env>` | Restore the previous release and pre-migration snapshot from the most recent release-changing deployment attempt, requiring per-invocation `CONFIRM=rollback-<env>` to authorize database replacement. Resolve the pair once and check out its release commit before downloading; reject missing records, artifacts or snapshots before changing services or application data, and stop on failure. Optional `RELEASE_REVISION` and `BACKUP_SNAPSHOT` must be supplied together to override the pair. **Invokes:** `download`, `setup`, `image-check`, `restore-<env>`, `up-<env>`, then `verify-<env>` using that pair | Lifecycle |
| `down-<env>[-<container>]` | Selected services stopped, with configuration and data preserved. Without a container selector, stop the environment's services | Lifecycle |
| `rm-<env>` | Environment containers and data removed | Lifecycle |
| `backup-<env>` | Create an encrypted database snapshot with its database-compatible full release revision and immutable release identity stored in the backup repository, apply configured retention, and check backup integrity; no application deployment. Identify the logical deployment independently of the host name; reject an unknown compatible revision rather than inferring it from the checkout. Report the exact snapshot ID created | Data administration |
| `snapshots-<env>` | List restorable database snapshots and their release revisions for the configured logical deployment; no repository initialization, snapshot creation, or application-data changes. With `SELECT=newest ENV_FILE=<path>`, select the newest snapshot once and atomically write owner-only shell-quoted exports of `BACKUP_SNAPSHOT` and `RELEASE_REVISION`; reject missing or inconsistent release metadata without falling back, and invalidate an existing output file on failure | Data administration |
| `restore-<env>` | Replace the environment's database from `BACKUP_SNAPSHOT`, requiring an explicit snapshot ID and per-invocation `CONFIRM=restore-<env>`. Stop application writers before replacement and leave them stopped for explicit startup; use normal database startup and storage initialization. No application deployment or automatic migration reversal. **Invokes:** `down-<env>-<container>` for each configured writer, then `up-<env>-<container>` for the database service before restoring | Data administration |
| `migrate-<env>` | Apply existing migration files using the environment's application runtime, only after a successful backup; no migration-file generation or application deployment. Backup failure prevents migration. **Invokes:** `backup-<env>` | Data administration |
| `superuser-<env>` | Create an administrative application account interactively; no implicit migration or application deployment | Data administration |
| `check` | Run all configured source-quality gates across the selected application's components: formatting checks, lint, type checks, tests and coverage thresholds, migration consistency, and framework checks. The required gates are identical locally and in CI; no source fixes or deployment. **Invokes:** `format-check`, `lint`, `typecheck`, `migrations-check`, `framework-check`, then `test COVERAGE=1`, stopping on failure | Validation |
| `ci-ephemeral` | Reproduce unattended source and release acceptance using disposable resources; wait for readiness before runtime checks, stop on failure, and always clean up ephemeral resources. No publication or persistent deployment changes. **Invokes:** `setup`, `check`, `build`, `image-check`, `prepare-ephemeral`, `up-ephemeral`, `integration-test-ephemeral`, `verify-ephemeral`, then `rm-ephemeral` | Validation |
| `format-check` | Check configured formatting across the selected application's components; no source fixes | Validation |
| `lint` | Run configured lint and source-policy checks across the selected application's components; no automatic fixes | Validation |
| `typecheck` | Run configured type checks across the selected application's components; no source changes | Validation |
| `migrations-check` | Check model changes have matching migration source files; no file generation or database migration | Validation |
| `framework-check` | Run configured framework/configuration checks using isolated check configuration; no persistent application-data or schema changes | Validation |
| `test` | Run source test suites for all selected application components once, using isolated test data; fail on test failure. With `COVERAGE=1`, also produce coverage reports and enforce configured total and changed-line thresholds against the configured diff base in the same run; otherwise no coverage gate. No other source-quality gates or persistent deployment changes | Validation |
| `image-check` | Validate the recorded image set's immutable identities, architecture, application revision labels, and configured runtime requirements such as non-root users and required tools; report installed tool versions. No rebuild, pull, or changes to running deployments or their data | Validation |
| `backup-test-<env>` | Restore the latest backup into a temporary database on the environment's database server, check it is queryable, and remove only that temporary database. Record the tested snapshot and success time; fail on missing backups or restore/query failure, without falling back to an older snapshot. No replacement of application data, application startup, or image build | Validation |
| `verify-<env>` | Read-only checks of health, expected revision, and HTTP/static smoke behavior; no container lifecycle changes, schema migration, or application-data writes | Validation |
| `monitor-check-<env>` | Run one pass of configured operational checks, report results, update monitoring state, and emit configured transition notifications or heartbeats; no service restart, migration, image build, backup, or restore | Validation |
| `integration-test-<env>` | Application integration tests, including writes to its data | Validation |
| `deps-lock` | Resolve declared package constraints and image source versions into committed dependency lockfiles and immutable image digest pins for the selected application and optional profiles. Do not choose versions outside those declarations. Registry access is allowed for digest resolution; no application startup, deployment-image build, or running deployment changes | Source editing |
| `migrations` | Generate migration source files for intentional model changes; no application-schema or data changes | Source editing |
| `format` | Apply configured formatting and automatic fixes across the selected application's components; no tests or deployment | Source editing |
| `clean` | Remove regenerable tool caches in the current checkout only; preserve source, installed dependencies, credentials, release artifacts, container resources, and application data | Cleanup |
| `help` | List documented commands supported by the selected profile, their selector forms, and required inputs; no setup, service changes, or secret values. Default for bare `make` | Access and inspection |
| `profiles` | Print JSON with selected profile names at `selected.application` and `selected.backup`, and configured validation profile-name arrays at `validation.application` and `validation.backup`; no configuration edits, setup, or service changes | Access and inspection |
| `config-<env>` | Write the resolved Compose model, including configured optional services, to owner-only `.local/config/<env>.json`; print only its path. No service changes, builds, image pulls, or registry/API calls | Access and inspection |
| `history-<env>` | List recorded deployment attempts, including previous and intended revisions, immutable image identities, pre-migration snapshot IDs, and outcomes; no deployment or data changes, and no secret values | Access and inspection |
| `ps-<env>[-<container>]` | Display container status for the selected service, or all environment services when omitted | Access and inspection |
| `logs-<env>[-<container>]` | Display container logs for the selected service, or all environment services when omitted | Access and inspection |
| `shell-<env>[-<container>]` | Open a shell in the selected service's container, defaulting to the application service when omitted | Access and inspection |
| `console-<env>` | Open the environment's interactive application/framework console | Access and inspection |

Downloading published artifacts needs registry access; the production host uses
its own existing configuration and credentials. Downloading does not build images
or rerun the build host's tests.
Subsequent commands run from that same checkout. Selecting or checking
out another revision alone does not change a running deployment.

SSH transfer and remote coordination belong to infra.

## Validation boundaries

- Source-quality checks must not also deploy or verify a running application.
- `verify-<env>` must not also run source-quality gates.
- Source-quality checks must not start, stop, migrate, or replace persistent
  deployments, use their application data, or build deployment images.
- Checks must not rewrite tracked source. Formatting and automatic fixes must
  be separate, explicitly invoked commands.

## Filesystem ownership

Services and workers use dedicated service UIDs, never a human user's UID.
Paths used by one identity are owned by that identity, using `chown` as needed,
with owner-only access. Paths shared by multiple services or host tools use a
shared group, with only the required group read, write, and directory-traversal
permissions. Private paths grant no access to unrelated users.

## Environments and resource names

| Environment | Runtime and data | Default Compose project |
| --- | --- | --- |
| `dev` | Docker containers running checked-out source and development servers, with persistent development data; no host-native application runtime or separate application image build required | `<project>-dev` |
| `prod` | Recorded immutable build, production server/ingress, persistent configuration and application data | `<project>-prod` |
| `ephemeral` | Same recorded build and server/ingress as prod; disposable configuration and data | `<project>-ephemeral` |

This table defines environment differences. For example, a laptop production
exercise uses `prod`; CI is an execution context, not another environment.

These are the only three default Docker Compose namespaces. A suffixed command
operates only on containers in its environment's project, including any helper
containers.

Profiles and application services do not create additional environment
namespaces. Ephemeral validation owns only its disposable resources; its cleanup
must never stop production or remove development/production data. Renaming a
project does not migrate its volumes: existing installations need an explicit
migration plan before defaults change.
