# Remote production database for laptop staging

This optional profile lets a laptop-hosted staging stack use a PostgreSQL
database on a remote production host without publishing PostgreSQL publicly.
It does not deploy production, select releases, run migrations, or manage
shared ingress.

The production overlay publishes the existing `db` service only on the remote
host's loopback interface. A laptop-side Compose service opens an SSH local
forward to that loopback listener and exposes the forwarded socket only inside
the staging Compose network:

```text
staging application -> production-db-tunnel:5432
                    -> SSH
                    -> remote 127.0.0.1:15432
                    -> production PostgreSQL
```

Stopping the laptop, staging stack, or tunnel cannot stop production. Nothing
in this profile creates a connection from production back to the laptop.

## Adopt the profile

Keep the profile at `profiles/remote-production-db` and include its Make
contract after the consuming project defines `COMPOSE_STAGING`:

```make
include profiles/remote-production-db/profile.mk
```

On the remote production host, add the production overlay to the existing
production Compose file list:

```make
PROD_COMPOSE_FILES += -f profiles/remote-production-db/production.compose.yml
```

`REMOTE_DB_LOOPBACK_PORT` defaults to `15432`. The resulting publication is
`127.0.0.1:15432`; do not change it to `0.0.0.0` or add a public firewall rule.
The production database volume, name, user, and password do not change.

On the laptop, add the staging overlay to the existing staging Compose command:

```make
STAGING_COMPOSE_FILES += -f profiles/remote-production-db/staging.compose.yml
COMPOSE_STAGING = COMPOSE_PROJECT_NAME=$(STAGING_PROJECT) docker compose \
    $(STAGING_COMPOSE_FILES) --env-file deploy/.env.staging
```

The staging application should use its existing production database identity
with only the hostname and port changed to
`production-db-tunnel:5432`. Make it depend on
`production-db-tunnel` with `condition: service_healthy`. Do not run staging
migrations.

Provide these laptop-private inputs through the selected staging environment:

```dotenv
STAGING_DB_SSH_HOST=production-host-ssh-alias
STAGING_DB_SSH_PORT=22
STAGING_DB_SSH_USER=operator
STAGING_DB_SSH_IDENTITY_FILE=/absolute/path/to/private/key
STAGING_DB_SSH_KNOWN_HOSTS_FILE=/absolute/path/to/known_hosts
REMOTE_DB_HOST=127.0.0.1
REMOTE_DB_PORT=15432
```

The private key and `known_hosts` file are mounted read-only and are not copied
into the image. `StrictHostKeyChecking`, `BatchMode`, `IdentitiesOnly`, and
`ExitOnForwardFailure` are always enabled. Prefer a dedicated SSH key restricted
on the remote host to local forwarding and
`permitopen="127.0.0.1:15432"`. SSH account and firewall ownership remain with
the host infrastructure operator.

## Operate the tunnel

Start and verify it before starting staging:

```bash
make up-staging-db-tunnel
make status-staging-db-tunnel
```

The first command waits for a working PostgreSQL connection through SSH. Start
the consuming project's normal staging workflow only after it succeeds. For
diagnostics and shutdown:

```bash
make logs-staging-db-tunnel
make down-staging-db-tunnel
```

The down target removes only the laptop tunnel container. It does not stop
staging, production, or either database volume. Staging database operations
fail while the tunnel is offline; production continues normally.

## Live-production database limitations

This topology provides transport privacy, not staging isolation:

- Staging can read, change, lock, or delete real production data with the
  configured database permissions.
- Staging code must work with the currently deployed production schema. Never run candidate migrations from the laptop.
- Use expand/contract migrations. Apply a backward-compatible schema change in
  production before staging code requires it, and keep candidate writes
  readable by the current production revision.
- Staging request side effects are production side effects. Disable or carefully
  control background work, external notifications, and destructive operations.
- Tunnel latency or interruption can reset staging connections and transactions.
  Staging load and locks can still affect production performance.

## Local exercise

The exercise creates separate Compose projects and networks for simulated
remote production and laptop staging. PostgreSQL uses the profile's exact
loopback-only publication; the staging probe reaches it through a separately
loopback-published SSH service and the tunnel. All generated keys and state stay under
`.tmp/remote-production-db-exercise`.

```bash
make -f profiles/remote-production-db/profile.mk exercise-remote-db-tunnel
make -f profiles/remote-production-db/profile.mk status-remote-db-tunnel-exercise
make -f profiles/remote-production-db/profile.mk logs-remote-db-tunnel-exercise
make -f profiles/remote-production-db/profile.mk down-remote-db-tunnel-exercise
```

The exercise proves that staging reads the remote sentinel through SSH, loses
database access after the tunnel stops, and leaves the remote database healthy
and readable.
