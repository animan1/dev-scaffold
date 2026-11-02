# Makefile command contract

Proposed contract for the Makefile redesign. This document does not change
existing targets. Implement the agreed contract in subsequent PRs.

## Command names

Environment-specific targets use `<command>-<env>`. The environment comes last.
For example, `make up-prod` deploys production on the current host;
`make shell-prod` opens a shell there. `make prod-shell` violates the convention.

Environment-neutral commands, such as `make help`, have no environment suffix.
An environment-specific command must name its environment explicitly; bare
`make up` is not a canonical command under this contract.

## Command types

| Type | Purpose | Example |
| --- | --- | --- |
| Deployment | Change the running application on the current host | `up-prod` |
| Build | Produce images or other artifacts | `build-release` |
| Validation | Check source, artifacts, or application behavior | `verify-release` |
| Access and inspection | Work with an existing environment | `shell-prod`, `logs-prod`, `ps-prod` |
| Lifecycle | Stop an environment while preserving its data | `down-dev` |

Building does not deploy. Validation does not replace a running development or
production deployment. A command's documented purpose must disclose any
temporary services it starts. SSH transfer and remote coordination belong to
infra, not these host-local commands.

## Environments and resource names

| Environment | Purpose | Default Compose project |
| --- | --- | --- |
| `dev` | Local development | `<project>-dev` |
| `prod` | The deployed application, including a laptop production exercise | `<project>-prod` |
| `release` | Testing deployable images and bundles | `<project>-release` |

These are the only three default environment namespaces. `up-prod` and
`deploy-release` must address the same production project; `deploy-release` is
an existing name to reconcile during target migration, not a fourth environment.
CI is an execution context, not another environment.

Profiles and application services do not create additional environment
namespaces. Release validation owns only its disposable resources; its cleanup
must never stop production or remove development/production data. Renaming a
project does not migrate its volumes: existing installations need an explicit
migration plan before defaults change.
