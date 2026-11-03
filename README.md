# Dev Scaffold

General-purpose Django + React + Docker + VS Code scaffold. Fork to start new projects.

The committed `.scaffold-profile` switch selects a project's active profile
without copying or deleting files. Keeping inactive profile implementations
tracked allows derived projects to merge later scaffold improvements normally.
Projects that render HTML in Django can select the optional
[server-rendered Django profile](profiles/server-rendered-django/README.md),
which uses PostgreSQL and the same full-Docker Make contract in development and
CI without activating the React/Vite toolchain.

For an existing repository, follow the provenance-preserving
[initial adoption guide](docs/adopting-into-an-existing-repository.md) before
application work. Once a project contains real code, use the separate
[established-project upgrade guide](docs/upgrading-an-established-project.md).

## Features

- **Backend**: Django with type hints, Ruff formatting and linting, MyPy type checking, pytest, and coverage enforcement.
- **Frontend**: React (Vite + pnpm) with ESLint, Prettier, and TypeScript.
- **Dev Experience**:
  - Unified `Makefile` with explicit environment selectors (`up-dev`, `up-prod`, `verify-prod`).
  - Pre-commit hooks run the same read-only `check` gates as CI.
  - VS Code integration via workspace settings.
  - `.editorconfig` for consistent formatting across tools/editors.
- **Deployment**:
  - Dockerized backend and frontend.
  - Multi-stage Nginx image builds and serves the frontend.
  - Docker Compose setups for dev and prod.
  - Loopback origins behind separately owned TLS ingress.
- **CI**:
  - Uses Make targets for reproducibility.
  - The same `ci-ephemeral` source and release acceptance used locally.
  - Smoke tests run against containers to validate API, static, and frontend.

## Quickstart

### Docker Dev

The host needs Git, Make, Python 3.12+ (standard-library orchestration only), and
Docker with Compose and the containerd image store. Infra owns engine
configuration; scaffold commands reject the classic store. Application tools run in containers.

```bash
make setup
make prepare-dev
make up-dev
make verify-dev
make down-dev  # preserve development data
```

`make rm-dev` deliberately removes the environment's containers and volumes.
Use `make check` for source validation and `make hooks` to install it as the
pre-commit hook. Commands never apply source fixes unless you invoke `make format`.

### Docker Prod (local)

```bash
make prepare-prod
make ci-ephemeral  # setup, check, committed build, image/runtime checks, cleanup
make up-prod       # deploy the tested images; preserve configuration and volumes
make verify-prod
make down-prod     # stop while preserving data
```

Commit the intended source before building. For individual commands, published
releases, backup/rollback and new-host recovery, use the
[command contract](docs/makefile-design.md) and
[workflows](docs/makefile-workflows.md).

### Immutable releases

Production uses recorded immutable images. Local builds and GHCR releases use
the same deployment commands; startup never builds or runs writable tests.

CI reads the committed scaffold selector and validates its configured profiles
before publishing the selected application's exact tested image set.
Both profiles retain the same two-package contract (`-backend` and `-web`),
tagged with the full commit SHA, but each profile supplies its own build and
production topology:

- `react-vite` keeps the existing Django backend plus built React/Vite Nginx
  image and release Compose path unchanged.
- `server-rendered-django` builds a non-root Gunicorn image and a non-root,
  application-owned Nginx image. Nginx proxies Django, serves collected static
  files, and serves uploaded media from a persistent volume mounted read-only.

CI verifies and smoke-tests the exact selected images before pushing them to
the repository's GHCR namespace. A successful `main` run publishes a GitHub
Release containing digest-pinned image references, release identity
metadata, and an SPDX JSON SBOM for each image. When GitHub artifact
attestations are available, the workflow also signs each SBOM and attaches the
attestation to the corresponding image digest in GHCR. Public repositories
enable attestations automatically. Private and internal repositories use the
fallback by default, regardless of whether a user or organization owns them;
they do not need to become public or move to an organization.

GitHub Enterprise Cloud can provide artifact attestations to eligible private
and internal repositories, but that plan capability cannot be inferred
reliably from repository visibility or owner type. After confirming support,
an organization-owned repository can set the Actions variable
`ENABLE_NONPUBLIC_ATTESTATIONS` to the exact value `true` to opt in. The opt-in
does not enable attestations for user-owned private repositories. Leave it
unset or set it to `false` everywhere else.

The private-repository fallback preserves exact digest selection and both
downloadable SBOMs, but it provides a reduced provenance guarantee: the SBOMs
are not cryptographically bound by GitHub to the repository and workflow that
produced them. Consumers must instead trust access to the Actions run and its
retained release artifact, and independently confirm that the manifest's image
digests match GHCR. The public health endpoint reports release identity so
deployment automation can compare environments without application-data
access.

On a prepared destination, `make download-latest`, `make setup`, `make up-prod`
and `make verify-prod` select and deploy the newest successful main release.
`make download` instead obtains the current checkout's exact release without
changing Git HEAD. Both reject unavailable artifacts rather than choosing an
older release. Authenticate only to the tenant's GHCR packages when downloading.

`make history-prod` inspects deployment records. `make rollback-prod
CONFIRM=rollback-prod` restores the recorded previous release and pre-migration
database snapshot. This replaces database contents; migrations are not assumed
reversible. See the workflows for application-only rollback and disaster recovery.

The monitoring package also includes the staged-release comparison proven in
Cuplr. Given staging and production review URLs, it derives each
`/api/healthz` endpoint, verifies the release metadata, waits for the configured
bake period, and returns a prioritized promotion action only when staging is
actually ahead. Optional CA-file settings support separately owned local TLS
boundaries without duplicating status URLs.

For repositories where attestations are enabled, verify a published image's
signed SBOM attestation against the repository that built it:

```bash
gh attestation verify oci://ghcr.io/<owner>/<repo>-backend@<digest> \
  --repo <owner>/<repo>
```

Set `PROJECT_NAME` when a host runs multiple projects. Its default is
derived from the current repository directory so unrelated projects do not
share Compose resources.

### Optional separately owned host ingress

Production can expose an HTTP-only origin on a configurable loopback port for a
separately managed host ingress. The external proxy owns public TLS and routes a
hostname to that origin; the application does not bind a public port or mount a
certificate in this profile.

For `server-rendered-django`, that loopback origin is the application-owned
Nginx sidecar. The host proxy does hostname routing and TLS only. It receives no
application secrets, volume access, or Docker-socket access; static and media
delivery remain inside the application's Compose boundary.

```bash
make config-prod
make up-prod
make verify-prod
make down-prod
```

Set `APP_PORT=18080` and `HOST_INGRESS_HOST=app.example.com` in the private
production env file. Use `make config-prod` to inspect the resolved model.

Configure the host ingress to route `app.example.com` to
`http://127.0.0.1:18080` and to replace the `Host`, `X-Forwarded-Proto`, and
`X-Forwarded-For` headers. Do not append client-supplied forwarding headers.
Add the public hostname to `DJANGO_ALLOWED_HOSTS`; HTTPS origins used for unsafe
requests must also be in `DJANGO_CSRF_TRUSTED_ORIGINS`. The loopback binding is
the trust boundary, so do not republish the origin port on a public interface.
The smoke target supplies synthetic routing headers and does not require or
prescribe a particular ingress product.

The React/Vite profile's application Nginx renders
`deploy/nginx/default.host-ingress.conf` at container startup. `BACKEND_HOST`
defaults to the production Compose service name `backend`; an overlay may select
another service name without copying the template. Substitution is deliberately
restricted to `BACKEND_HOST`, preserving Nginx runtime variables such as `$host`
and `$remote_addr`. API and Django admin requests go to that backend, while
static files, built frontend assets, and client-side routes remain owned by
Nginx. Docker DNS resolution refreshes the backend address after replacement.
Its bare `/admin` redirect remains relative so the private
HTTP origin never downgrades the public HTTPS scheme.

Existing React/Vite children adopting this behavior should copy the host-ingress
Compose overlay and Nginx template together with the command implementation.
Keep the child's hostnames, loopback ports,
Compose identity, release registry, CSP additions, and application-specific
promotion or backup checks in the child. Server-rendered children already use
their profile-owned Nginx release topology and should retain that boundary.

### Optional operational and dead-man monitoring

The scaffold's Django `app.monitoring` architecture provides persisted check
state, failure/recovery transitions, the
`monitor_operational_integrity` management command, backup/restore freshness
checks, and a restartable Compose `monitor` service. Projects add their own
domain checks to `operational_checks()` without replacing that infrastructure.

The optional production profile reports aggregate operational health to an
external monitoring provider with content-free HTTPS POSTs. Create three
Healthchecks-compatible checks at the provider: one each for operational
availability, successful backups, and successful isolated restore
verification. Enable failure and recovery notifications at the provider. The
same transition service also retains a generic email backend for projects that
select `MONITOR_NOTIFICATION_BACKEND=email`; the external production profile
does not use application SMTP.

Store each secret ping URL in its own project-local file; never put a ping URL
in `.env.prod` or a Compose environment value:

```bash
install -d -m 0700 deploy/secrets
printf '%s\n' 'https://provider.example/ping/operational-id' \
  > deploy/secrets/monitor-operational-url
printf '%s\n' 'https://provider.example/ping/backup-id' \
  > deploy/secrets/monitor-backup-url
printf '%s\n' 'https://provider.example/ping/restore-id' \
  > deploy/secrets/monitor-restore-url
chmod 0600 deploy/secrets/monitor-*-url
make ops.check-external-prod
```

The files are ignored by Git and mounted read-only under `/run/secrets`. For
secrets stored elsewhere, set `MONITOR_OPERATIONAL_URL_PATH`,
`MONITOR_BACKUP_URL_PATH`, and `MONITOR_RESTORE_URL_PATH` to host file paths.

Configure the provider schedules to match these bounded defaults:

| Check | Expected period | Grace period |
| --- | ---: | ---: |
| Operational | 5 minutes | 10 minutes |
| Backup | 24 hours | 6 hours |
| Restore verification | 7 days | 24 hours |

Intervals are constrained to 60 seconds through 7 days; grace periods are
constrained to 60 seconds through 24 hours. Override a value in `.env.prod`
with `MONITOR_<CHANNEL>_INTERVAL_SECONDS` or
`MONITOR_<CHANNEL>_GRACE_SECONDS`, update the provider to the same values, then
run `make ops.check-external-prod` again.

Start the restartable operational watcher explicitly:

```bash
make up-prod MONITORING=1
make ops.monitor-prod
make down-prod MONITORING=1
```

Backup and restore-verification jobs should wrap their real command so the
provider receives `start`, `fail`, and success events while the command's exit
status is preserved:

```bash
python -m app.manage monitor_job backup -- ./backup-command
python -m app.manage monitor_job restore -- ./isolated-restore-verification
```

Those job containers need only their channel's URL file mounted read-only and
the matching `MONITOR_<CHANNEL>_URL_FILE` environment variable pointing to it.
A success after a failure or missed deadline is the provider's recovery
signal. The wrapper preserves the underlying job's exit status even if the
monitoring provider is unavailable.

## Make Targets

```bash
make help
```

## Environment

- **Backend**: Reads environment via `django-environ`. No defaults for secrets in prod.
- **Frontend**: Uses Vite. Environment variables prefixed with `VITE_`.
- **Dev**: SQLite default.
- **Prod**: Postgres required (`deploy/.env.prod`).

## Directory Layout

```
backend/      # Django project
frontend/     # React app (Vite)
deploy/       # nginx configs, docker-compose
.vscode/      # VS Code workspace settings
```

## Contributing / Forking

- Fork this repo for new projects.
- Update `README.md` with project-specific details.
- Adjust Django apps, models, and React components as needed.
- Replace the TLS certs in `deploy/nginx/certs` with your real ones in prod.

---
