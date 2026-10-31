# Cuplr adoption: remote production database for laptop staging

Apply this only after the remote-production database profile is merged into
Cuplr. It deliberately leaves Cuplr's deployment and promotion commands
unchanged.

1. Import `profiles/remote-production-db` from dev-scaffold.
2. On the Droplet, include `production.compose.yml` in Cuplr's existing
   production Compose file list. Keep the default loopback binding or choose a
   private `REMOTE_DB_LOOPBACK_PORT`; never publish it on a public address.
3. On the laptop, include `staging.compose.yml` in Cuplr's existing
   `COMPOSE_STAGING` command while retaining the `cuplr-staging` project name.
4. Include `profile.mk` after `COMPOSE_STAGING` is defined so its tunnel targets
   dispatch through Cuplr's selected staging Compose contract.
5. In Cuplr's private staging environment, retain the current production
   PostgreSQL database name, user, and password, but change the database host to
   `production-db-tunnel` and port to `5432`. Add the SSH inputs documented in
   the profile README.
6. Make `staging-backend` depend on the healthy `production-db-tunnel` service.
   Keep staging off the remote production Docker network; SSH replaces that
   shared-host network attachment.
7. Do not add a database service or volume to laptop staging and do not run
   migrations there.

Operator sequence:

```bash
make up-staging-db-tunnel
make status-staging-db-tunnel
make deploy-staging
make status-staging
```

When staging is no longer needed:

```bash
make down-staging
make down-staging-db-tunnel
```

Before accepting the Cuplr adaptation, stop the tunnel and confirm staging
loses database access while the Droplet's production health and database remain
available. Restart the tunnel and confirm staging reports the same database
identity and expected existing data. Exercise no destructive write against the
production dataset.

Cuplr owns the staging-specific database URL, application safeguards, migration
sequencing, and any monitoring behavior when laptop staging is offline.
cranescastle-infra owns the Droplet's SSH authorization, firewall, tenant
registration, shared ingress, and DNS. The scaffold profile owns only the
loopback publication, SSH tunnel container, Make targets, and local contract
exercise.
