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
| Lifecycle (including deployment) | Start, update, or stop an environment; preserve its data | `up-prod`, `down-dev` |
| Build | Produce images or other artifacts | `build-prod` |
| Validation | Check source, artifacts, or application behavior | `verify-ephemeral`, `verify-prod` |
| Access and inspection | Work with an existing environment | `shell-prod`, `logs-prod`, `ps-prod` |

Building does not deploy. Validation does not replace a running development or
production deployment. A command's documented purpose must disclose any
temporary services it starts. SSH transfer and remote coordination belong to
infra, not these host-local commands.

## Environments and resource names

| Environment | Purpose | Default Compose project |
| --- | --- | --- |
| `dev` | Local development | `<project>-dev` |
| `prod` | The deployed application, including a laptop production exercise | `<project>-prod` |
| `ephemeral` | Disposable validation of deployable images and bundles | `<project>-ephemeral` |

These are the only three default Docker Compose namespaces. A suffixed command
operates only on containers in its environment's project, including any helper
containers. CI is an execution context, not another environment.

Profiles and application services do not create additional environment
namespaces. Ephemeral validation owns only its disposable resources; its cleanup
must never stop production or remove development/production data. Renaming a
project does not migrate its volumes: existing installations need an explicit
migration plan before defaults change.

## Updating production on the current host

Starting with an older production version running and the new source committed,
run these proposed commands in order, stopping at the first failure. The same
sequence applies on a laptop, in CI, or on a droplet with Docker and the required
configuration; it requires no prior CI build or validation elsewhere.

```sh
make build-prod
make verify-ephemeral
make up-prod
make verify-prod
```

`build-prod` builds the committed source for the host's architecture and records
the candidate revision and immutable image identities, leaving prod running.
`verify-ephemeral` runs local source checks, then initializes, migrates, and
smoke-tests those production images with disposable data in `-ephemeral`. It
cleans up only that namespace and records success for that exact candidate.

`up-prod` requires that success, then runs the candidate's migrations and starts
those same images in `-prod`, preserving configuration and application volumes.
It neither rebuilds nor selects another candidate. `verify-prod` checks health,
smoke behavior, and the deployed revision without replacing containers. A failed
build or ephemeral check leaves the old prod running; migration failure stops the
update, but does not imply database changes can be reversed.

The public deployment command is `up-prod`. Migration and startup are its
substeps; this sequence needs no separate public `deploy-release` command.
