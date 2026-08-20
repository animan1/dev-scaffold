# Server-rendered Django profile

This optional profile is for applications whose HTML is rendered by Django.
It is selected through one committed switch; it does not copy, replace, or
delete scaffold files. The inactive React/Vite implementation stays tracked so
future scaffold merges can update both profiles normally.

## What the profile provides

- Full-Docker development with one Django application service and PostgreSQL.
- PostgreSQL for development, tests, and CI; the supported workflow does not
  use the development SQLite fallback.
- Project-scoped Compose resources and a configurable loopback-only port.
- A persistent PostgreSQL volume. `make down` preserves it; `make reset
  CONFIRM_RESET=1` is the explicit destructive operation.
- The same Make interface for developers and CI: `build`, `up`, `down`,
  `reset`, `deps.lock`, `format`, `lint`, `typecheck`, `test`, `coverage`,
  `check`, `verify`, `precommit`, `build-production`, and `smoke`.
- Ruff formatting and linting, strict MyPy, pytest, total and changed-line
  coverage, migration-drift checks, Django deployment checks, a multi-stage
  non-root production image, and routed smoke tests.
- The scaffold's optional monitoring code and loopback host-ingress boundary.

The active profile has no frontend service and invokes no React, Vite, pnpm,
or frontend quality gate. It contains no Wagtail or project-specific domain,
content, credential, hostname, path, or deployment configuration.

## Immutable release topology

The production Compose definition contains a two-image application set:

- `-backend` is the non-root Django/Gunicorn image. Django writes uploaded files
  to the persistent `media` volume, and Gunicorn is never published directly
  to a host port.
- `-web` is a non-root, application-owned Nginx image. It proxies all dynamic
  routes to Gunicorn and mounts `staticfiles` and `media` read-only.

The web origin binds only to `127.0.0.1:${RELEASE_HTTP_PORT}`. A separately
owned host reverse proxy routes the public hostname to that origin and owns
TLS. It does not join the application network, receive application credentials,
mount application volumes, or access the Docker socket.

The Compose definition does not hide application initialization in a container
entrypoint. Release automation must explicitly run Django's production checks,
`migrate`, and `collectstatic` with the selected backend image before starting
the full image set. In particular, `collectstatic` must populate the shared
`staticfiles` volume when that volume is new.

The selected profile supplies those explicit operations through the stable
release Make interface. `initialize-release-ci` creates the isolated test stack,
runs the production checks and migrations, and populates a fresh static volume.
`verify-release-images` then smoke-tests dynamic, static, and media routes before
and after stopping Gunicorn. Production deployment uses `initialize-release`
before `deploy-release`; rollback selects an older digest manifest and follows
the same initialization path. Neither path builds an image during deployment.

The downstream application adds Wagtail, models, upload forms, storage policy,
and backup/restore integration without changing this proxy boundary. A project
that replaces the local media volume with object storage owns that explicit
adaptation and its credentials; no object-storage or host-proxy credentials
belong in the generic scaffold web image.

## Select the profile

Edit `.scaffold-profile`:

```make
SCAFFOLD_PROFILE ?= server-rendered-django
CI_PROFILES ?= server-rendered-django
```

The first setting selects the behavior used by developers. The second lists
the profiles CI should verify. The scaffold repository lists both profiles so
it tests its full catalog; a derived project should list only its active
profile.

Do not copy or remove files. Leave the inactive `frontend/`, pnpm, and
React/Vite profile files tracked. They are dormant under this selector, and
retaining them prevents modify/delete conflicts when merging later scaffold
improvements.

Then exercise the profile through its stable contract:

```bash
make verify
make precommit
make deps.lock
```

`make deps.lock` updates only the selected profile's lockfile using a uv 0.8.17
container with the profile's Python 3.12 and Bookworm runtime. It does not
require uv on the host.

Set a unique project name and unused port when the directory defaults are not
suitable:

```bash
make up PROJECT_NAME=example-site APP_PORT=18123
make smoke PROJECT_NAME=example-site APP_PORT=18123
make down PROJECT_NAME=example-site APP_PORT=18123
```

## Downstream boundary

Downstream projects change the selector and add only application concerns.
For Crane's Castle, Wagtail, website settings, content models, logging,
middleware, and deployment values remain in its own PR. They do not belong in
this generic profile.
