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
  `reset`, `format`, `lint`, `typecheck`, `test`, `coverage`, `check`,
  `verify`, `precommit`, `build-production`, and `smoke`.
- Ruff formatting and linting, strict MyPy, pytest, total and changed-line
  coverage, migration-drift checks, Django deployment checks, a multi-stage
  non-root production image, and routed smoke tests.
- The scaffold's optional monitoring code and loopback host-ingress boundary.

The active profile has no frontend service and invokes no React, Vite, pnpm,
or frontend quality gate. It contains no Wagtail or project-specific domain,
content, credential, hostname, path, or deployment configuration.

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
```

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
