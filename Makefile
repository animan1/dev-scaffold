SHELL := /bin/bash
COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml
COMPOSE_PROD := docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod
FRONTEND_DIR := frontend

# ---- Paths ----
PY_DIR := backend
URL_ROOT ?= http://localhost:8080
SMOKE_FLAGS ?=

# ---- Help ----
help:
	@echo "Targets:"
	@echo "  setup           Install Python deps (uv) and pre-commit hooks"
	@echo "  hooks           Install pre-commit hooks"
	@echo "  be.verify          Run lint, typecheck, tests, coverage (all)"
	@echo "  preflight       Pre-commit all-in-one (format, lint, typecheck, tests, coverage, hooks)"
	@echo "  backend.run     Run Django dev server (app.settings.dev)"
	@echo "  migrate         Run Django migrations"
	@echo "  superuser       Create Django superuser"
	@echo "  lint            Ruff lint"
	@echo "  format          Apply Ruff format + Black"
	@echo "  fmt-check       Check formatting without changing files"
	@echo "  typecheck       Mypy type checking"
	@echo "  test            Pytest"
	@echo "  coverage        Pytest with coverage and gate"
	@echo "  precommit       Run pre-commit on all files"
	@echo "  clean           Remove caches and build artifacts"

# ---- Docker / Compose ----
.PHONY: up
up:
	$(COMPOSE_DEV) up --build -d

.PHONY: down
down:
	$(COMPOSE_DEV) down -v

.PHONY: restart
restart: down up

.PHONY: logs
logs:
	$(COMPOSE_DEV) logs -f --tail=200

.PHONY: ps
ps:
	$(COMPOSE_DEV) ps

.PHONY: bash-backend
bash-backend:
	$(COMPOSE_DEV) exec backend bash

.PHONY: wait-backend
wait-backend:
	@echo "Waiting for backend on http://localhost:8000/healthz ..."
	@for i in $$(seq 1 60); do \
		if curl -fsS http://localhost:8000/healthz >/dev/null; then \
			echo "Backend is up"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Backend did not become ready in time"; \
	$(COMPOSE_DEV) logs backend || true; \
	exit 1

# CI helper: run backend verify inside container
.PHONY: be.ci
be.ci:
	$(COMPOSE_DEV) run --rm backend bash -lc 'make -f /workspace/Makefile PY_DIR=/app be.verify'

# CI helper: run frontend verify inside container
.PHONY: fe.ci
fe.ci:
	$(COMPOSE_DEV) run --rm frontend sh -lc 'apt-get install make'
	$(COMPOSE_DEV) run --rm frontend sh -lc 'make -f /workspace/Makefile FRONTEND_DIR=/app fe.setup fe.verify'

.PHONY: smoke
smoke: CURL_CMD = curl $(SMOKE_FLAGS) -fsS
smoke:
	$(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/static/smoketest.txt" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/" | grep -qi 'dev-scaffold\|id="root"'

# One-shot CI recipe
.PHONY: ci
ci: up wait-backend be.ci fe.ci smoke
	@echo "CI checks passed"

.PHONY: ci-clean
ci-clean:
	-$(COMPOSE_DEV) down -v

# Prod
.PHONY: bootstrap-prod
bootstrap-prod:
	@echo "🔧 Bootstrapping prod-local (env + certs + compose up + migrate + smoke)..."
	# Ensure env file exists (generate if missing)
	@if [ ! -f deploy/.env.prod ]; then \
		echo "→ Generating deploy/.env.prod"; \
		chmod +x deploy/setup-env.sh || true; \
		./deploy/setup-env.sh; \
	else \
		echo "✓ deploy/.env.prod exists"; \
	fi
	# Ensure TLS certs exist (generate if missing)
	@if [ ! -f deploy/nginx/certs/server.crt ] || [ ! -f deploy/nginx/certs/server.key ]; then \
		echo "→ Generating self-signed TLS certs under deploy/nginx/certs"; \
		mkdir -p deploy/nginx/certs; \
		openssl req -x509 -nodes -newkey rsa:2048 \
		  -keyout deploy/nginx/certs/server.key \
		  -out deploy/nginx/certs/server.crt \
		  -days 365 \
		  -subj "/CN=localhost"; \
	else \
		echo "✓ TLS certs exist in deploy/nginx/certs"; \
	fi
	# Bring up stack, run migrations, and smoke test
	$(COMPOSE_PROD) up -d --build
	$(COMPOSE_PROD) run --rm backend bash -lc "cd /app && python -m app.manage migrate"
	curl -kfsS https://localhost/api/healthz >/dev/null && echo "✅ Smoke OK: https://localhost/api/healthz"

.PHONY: up-prod
up-prod:
	$(COMPOSE_PROD) up -d --build

.PHONY: down-prod
down-prod:
	$(COMPOSE_PROD) down -v

.PHONY: logs-prod
logs-prod:
	$(COMPOSE_PROD) logs -f --tail=200

.PHONY: migrate-prod
migrate-prod:
	$(COMPOSE_PROD) run --rm backend bash -lc "cd /app && python -m app.manage migrate"

.PHONY: bash-prod
bash-prod:
	$(COMPOSE_PROD) exec backend bash

.PHONY: smoke-prod
smoke-prod: URL_ROOT := https://localhost
smoke-prod: SMOKE_FLAGS := -k
smoke-prod: smoke

# ---- Django dev helpers ----
.PHONY: backend.run
backend.run:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage runserver 0.0.0.0:8000

.PHONY: migrate
migrate:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage migrate

.PHONY: superuser
superuser:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage createsuperuser

# ---- Setup / Hooks ----
.PHONY: setup
setup:
	cd $(PY_DIR) && uv sync --all-extras
	uv run pre-commit install

.PHONY: hooks
hooks:
	uv run pre-commit install

# ---- Quality gates ----
.PHONY: be.verify
be.verify: lint typecheck test coverage

.PHONY: preflight
preflight: format lint typecheck coverage precommit
	@echo "✅ Preflight complete."

.PHONY: lint
lint:
	cd $(PY_DIR) && uv run ruff check .

.PHONY: format
format:
	cd $(PY_DIR) && uv run ruff check --fix .
	cd $(PY_DIR) && uv run ruff format .
	cd $(PY_DIR) && uv run black .

.PHONY: fmt-check
fmt-check:
	cd $(PY_DIR) && uv run ruff format --check .
	cd $(PY_DIR) && uv run black --check .

.PHONY: typecheck
typecheck:
	cd $(PY_DIR) && uv run mypy .

.PHONY: test
test:
	cd $(PY_DIR) && uv run pytest -q

.PHONY: coverage
coverage:
	cd $(PY_DIR) && uv run pytest --cov=src --cov-report=term-missing

# ---- Pre-commit runner ----
.PHONY: precommit
precommit:
	uv run pre-commit run --all-files

# ---- Clean ----
.PHONY: clean
clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf $(PY_DIR)/.pytest_cache $(PY_DIR)/.mypy_cache

# ---- Frontend (Vite/React/TS) ----
.PHONY: fe.setup
fe.setup:
	@corepack enable || true
	cd $(FRONTEND_DIR) && pnpm install

.PHONY: fe.run
fe.run:
	cd $(FRONTEND_DIR) && pnpm dev

.PHONY: fe.build
fe.build:
	cd $(FRONTEND_DIR) && pnpm build

.PHONY: fe.lint
fe.lint:
	cd $(FRONTEND_DIR) && pnpm lint

.PHONY: fe.format
fe.format:
	cd $(FRONTEND_DIR) && pnpm format

.PHONY: fe.fmt-check
fe.fmt-check:
	cd $(FRONTEND_DIR) && pnpm fmt-check

.PHONY: fe.test
fe.test:
	cd $(FRONTEND_DIR) && pnpm test

.PHONY: fe.typecheck
fe.typecheck:
	cd $(FRONTEND_DIR) && pnpm typecheck

.PHONY: fe.verify
fe.verify:
	cd $(FRONTEND_DIR) && pnpm verify
