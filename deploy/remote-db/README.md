# Remote PostgreSQL over SSH

The optional `remote-db-tunnel` service builds through the
normal application build/start workflow; it has no separate Make profile or
commands. One container serves the application's consumers on its Compose
network, without publishing a host port.

## Project configuration and host inputs

In a derived application's committed `.scaffold-profile`, set
`REMOTE_DB_TUNNEL ?= 1`. The base Makefile selects the shared tunnel and the
shared `consumer.compose.yml` settings through a service-name adapter (`backend`
or `app`). The shared definition does not create another runtime service.
This is project topology, configured once and committed with the project;
no `.local` Make settings or copied Compose files are required.

Host-specific SSH endpoints and key paths belong in private env files. The
inactive defaults in [`backend/.env.example`](../../backend/.env.example) do not
affect ordinary local databases while `REMOTE_DB_TUNNEL` is `0` (the default).
When enabled, interactive `make prepare-prod` prompts for missing/invalid SSH
inputs and a `DATABASE_URL` using `remote-db-tunnel:5432`. It saves the selections
privately, hides the URL while entering credentials, and retries invalid entries
until valid or aborted. Key and known-hosts paths must be existing readable files.
Unattended setup requires these inputs upfront and fails by field name otherwise.
`REMOTE_DB_SSH_PORT`
defaults to `22`; `REMOTE_DB_HOST` and `REMOTE_DB_PORT` default to
`127.0.0.1:15432`, as seen from the SSH server. The Makefile supplies the checkout
build context. Preparation never starts containers or contacts the remote database.

For the default React/Vite production workflow, preparation creates a missing
`deploy/.env.prod` and collects the required host inputs (preserving existing
application settings). Use bare commands:

```sh
make prepare-prod
make up-prod
make logs-prod
make down-prod
```

Preparation validates the resolved private configuration; startup builds the
tunnel with the existing stack and waits for it before starting the backend.
Shutdown preserves volumes and does not stop the remote database. Keep the same
Compose project identity. A monitor or other database consumer needs the same
healthy-tunnel dependency. Configure backup ownership explicitly: the existing
local `db` backup does not protect the selected remote database.

For development with either application profile, put host inputs in private
`backend/.env`, then use bare `make up`, `make ps`, `make logs` and `make down`.
Both profiles inherit their ordinary startup command; migrations remain explicit.
Health checks verify PostgreSQL readiness through SSH, not
database credentials or schema compatibility. Application-specific migration
policy remains with the downstream app.

If the remote database is the scaffold's existing `db` service, add
`deploy/remote-db/listen.compose.yml` to that host's existing Compose file list.
It publishes only `127.0.0.1:${REMOTE_DB_LOOPBACK_PORT:-15432}:5432`, preserving
the existing database volume and configuration. An already accessible remote
PostgreSQL endpoint needs no server-side scaffold overlay.

The key and verified `known_hosts` file are read-only bind mounts. Missing files
fail rather than creating directories. Strict host verification, noninteractive
authentication and forwarding failure checks are mandatory. Prefer a dedicated
SSH identity restricted to forwarding to the selected database address/port.
Host infrastructure owns SSH authorization and firewall policy.

The tunnel provides transport privacy, not data isolation: clients can change
real remote data. Use compatible application/schema versions, restrict database
permissions and side effects, and never run candidate migrations against a live
database without its normal deployment authorization. A tunnel outage interrupts
client connections; stopping the consuming stack cannot stop the remote database.
