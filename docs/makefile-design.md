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
- Access and inspection: interact with an existing environment.

## Command results

This table defines command behavior in every supported environment. Examples
in [Makefile workflows](makefile-workflows.md) illustrate composition and do not add requirements.
Subcommands are listed as **Invokes:** at the end of the result.

| Command | Result | Type |
| --- | --- | --- |
| `setup` | Prepare tooling and dependencies for the selected application's components from committed lockfiles; no lockfile edits, application startup, or deployment-image build | Setup |
| `hooks` | Install the repository's configured Git hooks for the current checkout; no hook execution, lockfile edits, or service startup | Setup |
| `build` | Immutable images of the committed checkout for the host architecture, with revision and image identities recorded; shared across environments, with no application startup | Build |
| `publish` | Upload the recorded build's images, committed deployment source, release manifest, and successful validation evidence; preserve their revision and immutable image identities | Artifact transfer |
| `download` | Obtain the selected published release's recorded images, manifest, evidence, and deployment source; prepare the current deployment checkout at the recorded revision and validate completeness, integrity, and architecture, without changing running containers or application data | Artifact transfer |
| `up-<env>[-<container>]` | Selected services running, matching the checked-out revision; retain configuration and volumes, and apply required migrations before application startup, without building or testing. Without a container selector, start all configured services and initialize dependencies. Migration failure stops startup; database changes are not automatically reversed. **Invokes:** `migrate-<env>` (before application startup) | Lifecycle |
| `restart-<env>[-<container>]` | Stop and start selected services using normal startup behavior, preserving configuration and data; stop on failure. **Invokes:** `down-<env>[-<container>]`, then `up-<env>[-<container>]` with the same selectors | Lifecycle |
| `down-<env>[-<container>]` | Selected services stopped, with configuration and data preserved. Without a container selector, stop the environment's services | Lifecycle |
| `rm-<env>` | Environment containers and data removed | Lifecycle |
| `backup-<env>` | Create an encrypted database snapshot, apply configured retention, and check backup integrity; no application deployment | Data administration |
| `snapshots-<env>` | List restorable database snapshots; no repository initialization, snapshot creation, or application-data changes | Data administration |
| `restore-<env>` | Replace the environment's database from `BACKUP_SNAPSHOT`, requiring an explicit snapshot ID and per-invocation `CONFIRM=restore-<env>`. Stop application writers before replacement and leave them stopped for explicit startup; no image deployment or automatic migration reversal | Data administration |
| `migrate-<env>` | Apply existing migration files using the environment's application runtime, only after a successful backup; no migration-file generation or application deployment. Backup failure prevents migration. **Invokes:** `backup-<env>` | Data administration |
| `superuser-<env>` | Create an administrative application account interactively; no implicit migration or application deployment | Data administration |
| `check` | Run all configured source-quality gates across the selected application's components: formatting checks, lint, type checks, tests and coverage thresholds, migration consistency, and framework checks. The required gates are identical locally and in CI; no source fixes or deployment. **Invokes:** `format-check`, `lint`, `typecheck`, `migrations-check`, `framework-check`, then `test COVERAGE=1`, stopping on failure | Validation |
| `format-check` | Check configured formatting across the selected application's components; no source fixes | Validation |
| `lint` | Run configured lint and source-policy checks across the selected application's components; no automatic fixes | Validation |
| `typecheck` | Run configured type checks across the selected application's components; no source changes | Validation |
| `migrations-check` | Check model changes have matching migration source files; no file generation or database migration | Validation |
| `framework-check` | Run configured framework/configuration checks using isolated check configuration; no persistent application-data or schema changes | Validation |
| `test` | Run source test suites for all selected application components once, using isolated test data; fail on test failure. With `COVERAGE=1`, also produce coverage reports and enforce configured thresholds in the same run; otherwise no coverage gate. No other source-quality gates or persistent deployment changes | Validation |
| `verify-<env>` | Read-only checks of health, expected revision, and HTTP/static smoke behavior; no container lifecycle changes, schema migration, or application-data writes | Validation |
| `monitor-check-<env>` | Run one pass of configured operational checks, report results, update monitoring state, and emit configured transition notifications or heartbeats; no service restart, migration, image build, backup, or restore | Validation |
| `integration-test-<env>` | Application integration tests, including writes to its data | Validation |
| `deps-lock` | Update the selected application's dependency lockfiles from declared dependency constraints; no application startup or deployment-image build | Source editing |
| `migrations` | Generate migration source files for intentional model changes; no application-schema or data changes | Source editing |
| `format` | Apply configured formatting and automatic fixes across the selected application's components; no tests or deployment | Source editing |
| `help` | List documented commands supported by the selected profile, their selector forms, and required inputs; no setup, service changes, or secret values. Default for bare `make` | Access and inspection |
| `config-<env>` | Write the resolved Compose model, including configured optional services, to owner-only `.local/config/<env>.json`; print only its path. No service changes, builds, image pulls, or registry/API calls | Access and inspection |
| `ps-<env>[-<container>]` | Display container status for the selected service, or all environment services when omitted | Access and inspection |
| `logs-<env>[-<container>]` | Display container logs for the selected service, or all environment services when omitted | Access and inspection |
| `shell-<env>[-<container>]` | Open a shell in the selected service's container, defaulting to the application service when omitted | Access and inspection |
| `console-<env>` | Open the environment's interactive application/framework console | Access and inspection |

The production host needs registry access and its own existing configuration and
credentials. Downloading does not build images or rerun the build host's tests.
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

## Environments and resource names

| Environment | Runtime and data | Default Compose project |
| --- | --- | --- |
| `dev` | Checked-out source, development server, persistent development data; no separate application image build required | `<project>-dev` |
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
