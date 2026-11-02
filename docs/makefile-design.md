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

- Build: produce artifacts.
- Lifecycle: start, update (including deployment), stop, or remove an environment.
- Validation: test source, artifacts, or application behavior.
- Access and inspection: interact with an existing environment.

## Command results

This table defines command behavior in every supported environment. Examples
below illustrate composition and do not add requirements.

| Command | Result | Type |
| --- | --- | --- |
| `build` | Immutable images of the committed checkout for the host architecture, with revision and image identities recorded; shared across environments, with no application startup | Build |
| `up-<env>` | A running application matching the checked-out revision; retain configuration and volumes, initialize dependencies, and apply required migrations, without building or testing. Migration failure stops startup; database changes are not automatically reversed | Lifecycle |
| `down-<env>` | Stopped application, with configuration and data preserved | Lifecycle |
| `rm-<env>` | Environment containers and data removed. Supported for `ephemeral`; `dev` requires explicit confirmation naming the project and acknowledging data deletion; unsupported for `prod` | Lifecycle |
| `verify-<env>` | Read-only checks of health, expected revision, and HTTP/static smoke behavior; no container lifecycle changes, schema migration, or application-data writes | Validation |
| `integration-test-<env>` | Application integration tests, including writes to its data | Validation |
| `shell-<env>` | Open a shell in the environment's application container | Access and inspection |
| `logs-<env>` | Display the environment's container logs | Access and inspection |
| `ps-<env>` | Display the environment's container status | Access and inspection |

SSH transfer and remote coordination belong to infra.

## Environments and resource names

| Environment | Runtime and data | Default Compose project |
| --- | --- | --- |
| `dev` | Checked-out source, development server, persistent development data; no separate application image build required | `<project>-dev` |
| `prod` | Recorded immutable build matching the checked-out revision (otherwise refuse startup), production server/ingress, persistent configuration and application data | `<project>-prod` |
| `ephemeral` | Same recorded build and server/ingress as prod, with the same revision requirement; disposable configuration and data | `<project>-ephemeral` |

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

## Example: updating production on the current host

Starting with an older production version running and the new source committed,
run these proposed commands in order, stopping at the first failure. The same
sequence applies on a laptop, in CI, or on a droplet with Docker and the required
configuration; it requires no prior CI build or validation elsewhere.

```sh
make build
make up-ephemeral
make integration-test-ephemeral
make verify-ephemeral
make rm-ephemeral
make up-prod
make verify-prod
```

In this example, prod remains running during build and ephemeral testing; only
`up-prod` updates it. On a test failure, stop the update and use `rm-ephemeral`
for cleanup. No separate public `deploy-release` command is needed.
