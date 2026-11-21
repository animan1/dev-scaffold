# Remote PostgreSQL over SSH

Add `deploy/remote-db/compose.yml` to the consuming application's existing
Compose file list. The optional `remote-db-tunnel` service builds through the
normal application build/start workflow; it has no separate Make profile or
commands. One container serves the application's consumers on its Compose
network, without publishing a host port.

For this repository's default development workflow, select the overlay through
the existing Compose-command setting. Reuse that setting for startup, status,
logs and shutdown:

```sh
export REMOTE_DB_BUILD_CONTEXT="$PWD"
export REMOTE_DB_SSH_HOST=db-host.example.com
export REMOTE_DB_SSH_USER=operator
export REMOTE_DB_SSH_IDENTITY_FILE=/absolute/path/to/private/key
export REMOTE_DB_SSH_KNOWN_HOSTS_FILE=/absolute/path/to/known_hosts
make up COMPOSE_DEV="docker compose -f deploy/docker-compose.dev.yml -f deploy/remote-db/compose.yml"
make ps COMPOSE_DEV="docker compose -f deploy/docker-compose.dev.yml -f deploy/remote-db/compose.yml"
make logs COMPOSE_DEV="docker compose -f deploy/docker-compose.dev.yml -f deploy/remote-db/compose.yml"
make down COMPOSE_DEV="docker compose -f deploy/docker-compose.dev.yml -f deploy/remote-db/compose.yml"
```

Downstream applications can instead include the overlay in their existing
Compose configuration permanently. Supply inputs through their selected private
env file or shell. `REMOTE_DB_SSH_PORT` defaults to `22`; `REMOTE_DB_HOST` and
`REMOTE_DB_PORT` default to `127.0.0.1:15432`, as seen from the SSH server.
`REMOTE_DB_BUILD_CONTEXT` is the absolute checkout root so the overlay works
with either application profile's Compose file location.

Configure the application's database connection hostname/port as
`remote-db-tunnel:5432`, retaining the selected database name and credentials.
Declare a `depends_on` condition of `service_healthy` for each database consumer
in its existing Compose configuration. Health checks verify PostgreSQL readiness
through SSH, not database credentials or schema compatibility. Application-specific
connection configuration and migration policy remain with the downstream app.

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
