# Dev Scaffold

General-purpose Django + React + Docker + VS Code scaffold. Fork to start new projects.

## Features

- **Backend**: Django with type hints, Ruff formatting and linting, MyPy type checking, pytest, and coverage enforcement.
- **Frontend**: React (Vite + pnpm) with ESLint, Prettier, and TypeScript.
- **Dev Experience**:
  - Unified `Makefile` with common targets (`verify`, `check`, `test`, `smoke`, `up`, `down`, etc.)
  - Pre-commit hooks (Ruff, ESLint, Prettier, end-of-file fixes).
  - VS Code integration via workspace settings.
  - `.editorconfig` for consistent formatting across tools/editors.
- **Deployment**:
  - Dockerized backend and frontend.
  - Multi-stage Nginx image builds and serves the frontend.
  - Docker Compose setups for dev and prod.
  - HTTPS with self-signed certs for local prod-like testing.
- **CI**:
  - Uses Make targets for reproducibility.
  - Separate jobs for backend (lint, typecheck, test, coverage) and frontend (lint, typecheck, build).
  - Smoke tests run against containers to validate API, static, and frontend.

## Quickstart

### Local Dev (no Docker)

```bash
make setup verify # bootstrap the environment
make setup be.run # run Django
```

```bash
# run React
make fe.run
```

### Docker Dev

```bash
make up        # start backend + nginx (proxying API, static, and frontend)
make smoke     # run API, static, and FE smokes
make down      # stop stack
```

### Docker Prod (local)

```bash
make bootstrap-prod  # once
make up-prod         # build & start prod stack with nginx + Django + Postgres
make smoke-prod      # run API, static, and FE smokes
make down-prod       # stop stack
```

### Optional immutable releases

The default local-production workflow remains intentionally simple and builds
from the checkout. Projects that need a pull-only host can opt into the
immutable-release profile.

CI derives two packages from the repository name (`-backend` and `-web`), tags
them with the full commit SHA, verifies and smoke-tests those exact local
images, and only then pushes them to this repository's GHCR namespace. A
successful `main` run publishes a release artifact containing digest-pinned
image references and an SPDX JSON SBOM for each image. The workflow also signs
each SBOM as a GitHub artifact attestation and attaches it to the corresponding
image digest in GHCR.

Download that run's `release-<sha>` artifact onto the host. Keep the project's
production configuration in `deploy/.env.prod`, then deploy without building:

```bash
make deploy-release RELEASE_FILE=/path/to/release-<sha>.env
```

The release path pulls the recorded digests, runs migrations and static-file
collection with the recorded backend image, and starts Compose with
`--no-build`. Registry login is host- and project-specific; authenticate the
host only to the GHCR packages belonging to that project.

Rollback is selection of an older retained manifest, not a rebuild:

```bash
make rollback-release RELEASE_FILE=/path/to/previous-release.env
```

Verify a published image's signed SBOM attestation against the repository that
built it:

```bash
gh attestation verify oci://ghcr.io/<owner>/<repo>-backend@<digest> \
  --repo <owner>/<repo>
```

Set `RELEASE_COMPOSE_PROJECT` when a host runs multiple projects. Its default is
derived from the current repository directory so unrelated projects do not
share Compose resources.

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
