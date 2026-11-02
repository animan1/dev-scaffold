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

## Command results

| Command | Result | Type |
| --- | --- | --- |
| `build` | Immutable images of the committed checkout for the host architecture, with revision and image identities recorded | Build |
| `up-<env>` | A running application matching the checked-out revision; initialize dependencies and apply required migrations | Lifecycle |
| `down-<env>` | Stopped application, with configuration and data preserved | Lifecycle |
| `rm-<env>` | Environment containers and disposable data removed; supported only for `ephemeral` | Lifecycle |
| `verify-<env>` | Read-only checks of health, expected revision, and HTTP/static smoke behavior | Validation |
| `integration-test-<env>` | Application integration tests, including writes to its data | Validation |
| `shell-<env>`, `logs-<env>`, `ps-<env>` | Shell access, logs, or container status for that environment | Access and inspection |

Each command has the same result in every supported environment. `build` starts
no application; its images are shared by prod and ephemeral, with no separate
build per environment. `up` neither builds nor runs tests. `verify` never creates,
replaces, or removes containers, migrates schemas, or writes application data.
SSH transfer and remote coordination belong to infra.

## Environments and resource names

| Environment | Runtime and data | Default Compose project |
| --- | --- | --- |
| `dev` | Checked-out source, development server, persistent development data | `<project>-dev` |
| `prod` | Recorded immutable images, production server/ingress, persistent application data | `<project>-prod` |
| `ephemeral` | Same images and server/ingress as prod, disposable configuration and data | `<project>-ephemeral` |

Dev needs no separate application image build. Prod and ephemeral consume the
same recorded build matching the checked-out revision, or refuse to start.
A laptop production exercise still uses `prod`; CI is not another environment.

These are the only three default Docker Compose namespaces. A suffixed command
operates only on containers in its environment's project, including any helper
containers.

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
make build
make up-ephemeral
make integration-test-ephemeral
make verify-ephemeral
make rm-ephemeral
make up-prod
make verify-prod
```

The old prod remains running through build and ephemeral testing. After those
steps succeed, `up-prod` starts the same tested images with existing production
configuration and volumes. Migration failure stops the update; it does not imply
database changes can be reversed. If a test fails, `rm-ephemeral` remains an
explicit cleanup step, affecting only ephemeral resources.

The public deployment command is `up-prod`. Migration and startup are its
substeps; this sequence needs no separate public `deploy-release` command.
