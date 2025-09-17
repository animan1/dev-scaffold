# Dev Scaffold

General-purpose Django + React + Docker + VS Code scaffold. Fork to start new projects.

## Features

- **Backend**: Django with type hints, Black formatting, Ruff linting, MyPy type checking, pytest, and coverage enforcement.
- **Frontend**: React (Vite + pnpm) with ESLint, Prettier, and TypeScript.
- **Dev Experience**:
  - Unified `Makefile` with common targets (`verify`, `check`, `test`, `smoke`, `up`, `down`, etc.)
  - Pre-commit hooks (Black, Ruff, ESLint, Prettier, end-of-file fixes).
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
