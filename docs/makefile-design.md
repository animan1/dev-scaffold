# Makefile command contract

Proposed contract for the Makefile redesign. This document does not change
existing targets. Implement the agreed contract in subsequent PRs.

## Command names

Environment-specific targets use `<command>-<env>`. The environment comes last.
For example, `make up-prod` deploys production on the current host;
`make shell-prod` opens a shell there. `make prod-shell` violates the convention.

Environment-neutral commands, such as `make help` and `make build`, have no suffix.
An environment-specific command must name its environment explicitly; bare
`make up` is not a canonical command under this contract.

## Command types

- Setup: prepare local tooling and dependencies.
- Build: produce artifacts.
- Artifact transfer: publish or download recorded release artifacts.
- Lifecycle: start, update (including deployment), stop, or remove an environment.
- Validation: test source, artifacts, or application behavior.
- Source editing: explicitly update source or lockfiles.
- Access and inspection: interact with an existing environment.

## Command results

This table defines command behavior in every supported environment. Examples
in [Makefile workflows](makefile-workflows.md) illustrate composition and do not add requirements.

| Command | Result | Type |
| --- | --- | --- |
| `setup` | Prepare tooling and dependencies for the selected application's components from committed lockfiles; no lockfile edits, application startup, or deployment-image build | Setup |
| `build` | Immutable images of the committed checkout for the host architecture, with revision and image identities recorded; shared across environments, with no application startup | Build |
| `publish` | Upload the recorded build's images, committed deployment source, release manifest, and successful validation evidence; preserve their revision and immutable image identities | Artifact transfer |
| `download` | Obtain the selected published release's recorded images, manifest, evidence, and deployment source; prepare the current deployment checkout at the recorded revision and validate completeness, integrity, and architecture, without changing running containers or application data | Artifact transfer |
| `up-<env>` | A running application matching the checked-out revision; retain configuration and volumes, initialize dependencies, and apply required migrations, without building or testing. Migration failure stops startup; database changes are not automatically reversed | Lifecycle |
| `down-<env>` | Stopped application, with configuration and data preserved | Lifecycle |
| `rm-<env>` | Environment containers and data removed | Lifecycle |
| `check` | Run all configured source-quality gates across the selected application's components: formatting checks, lint, type checks, tests and coverage thresholds, migration consistency, and framework checks. The required gates are identical locally and in CI; no source fixes or deployment | Validation |
| `verify-<env>` | Read-only checks of health, expected revision, and HTTP/static smoke behavior; no container lifecycle changes, schema migration, or application-data writes | Validation |
| `integration-test-<env>` | Application integration tests, including writes to its data | Validation |
| `deps-lock` | Update the selected application's dependency lockfiles from declared dependency constraints; no application startup or deployment-image build | Source editing |
| `format` | Apply configured formatting and automatic fixes across the selected application's components; no tests or deployment | Source editing |
| `ps-<env>` | Display the environment's container status | Access and inspection |
| `logs-<env>` | Display the environment's container logs | Access and inspection |
| `shell-<env>` | Open a shell in the environment's application container | Access and inspection |

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
