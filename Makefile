SHELL := /bin/bash
COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml
COMPOSE_PROD := docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod
FRONTEND_DIR := frontend

# ---- Paths ----
PY_DIR := backend
URL_ROOT ?= http://localhost:8080
CURL_FLAGS ?=
VERBOSE ?= 0
FEATURED := 'up down restart logs verify smoke up-prod smoke-prod help help-verbose'

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help.
	@FEATURED=$(FEATURED); \
	ALL="$$(grep -E '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST))"; \
	printf "Make targets:\n\n"; \
	printf "  \033[1mFeatured\033[0m\n"; \
	for t in $$FEATURED; do \
	  echo "$$ALL" | grep -E "^$${t}:.*?## " | \
	    awk 'BEGIN {FS=":.*?## "} {printf "    \033[36m%-24s\033[0m %s\n", $$1, $$2}'; \
	done

.PHONY: help-verbose
help-verbose: ## Show full help (featured + everything else)
	@FEATURED=$(FEATURED); \
	ALL="$$(grep -E '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST))"; \
	printf "Make targets:\n\n"; \
	printf "  \033[1mFeatured\033[0m\n"; \
	for t in $$FEATURED; do \
	  echo "$$ALL" | grep -E "^$${t}:.*?## " | \
	    awk 'BEGIN {FS=":.*?## "} {printf "    \033[36m%-24s\033[0m %s\n", $$1, $$2}'; \
	done; \
	printf "\n  \033[1mEverything else\033[0m\n"; \
	REGEX="$$(printf '%s\n' $$FEATURED | paste -sd'|' -)"; \
	echo "$$ALL" | grep -v -E "^($$REGEX):" | sort -t: -k1,1 | \
	  awk 'BEGIN {FS=":.*?## "} {printf "    \033[36m%-24s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## (Docker) Start dev stack (backend + nginx + frontend proxy)
	$(COMPOSE_DEV) up --build -d

.PHONY: down
down: ## (Docker) Stop dev stack and remove volumes
	$(COMPOSE_DEV) down -v

.PHONY: restart
restart: ## (Docker) Restart dev stack
	down up

.PHONY: logs
logs: ## (Docker) Tail dev logs
	$(COMPOSE_DEV) logs -f --tail=200

.PHONY: ps
ps: ## (Docker) List dev containers
	$(COMPOSE_DEV) ps

.PHONY: be.bash
be.bash: ## (Docker) Shell into backend container
	$(COMPOSE_DEV) exec backend bash

.PHONY: be.wait
be.wait: ## Wait for backend health (dev)
	@echo "Waiting for backend on $(URL_ROOT)/api/healthz ..."
	@for i in $$(seq 1 60); do \
		if curl -fsS $(CURL_FLAGS) $(URL_ROOT)/api/healthz >/dev/null; then \
			echo "Backend is up"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Backend did not become ready in time"; \
	$(COMPOSE_DEV) logs backend || true; \
	exit 1

.PHONY: be.ci
be.ci: ## (Docker) Run backend verify inside dev container
	$(COMPOSE_DEV) run --rm backend bash -lc 'make -f /workspace/Makefile PY_DIR=/app be.verify'

.PHONY: fe.ci
fe.ci: ## (Docker) Run frontend verify inside dev container
	$(COMPOSE_DEV) run --rm frontend sh -lc 'apk add --no-cache make >/dev/null 2>&1 || true; corepack enable; make -f /workspace/Makefile FRONTEND_DIR=/app fe.setup fe.verify'

.PHONY: smoke
smoke: ## Dev smoketest (API + static + FE root)
	CURL_CMD = curl $(CURL_FLAGS) -fsS
	$(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/static/smoketest.txt" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/" | grep -qi 'dev-scaffold\|id="root"'

.PHONY: ci
ci: ## (Docker) One-shot CI recipe (dev stack + FE/BE verify + smoke)
	up be.wait be.ci fe.ci smoke
	@echo "CI checks passed"

# Prod
.PHONY: bootstrap-prod
bootstrap-prod: ## Generate prod env/certs and start prod stack
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

.PHONY: restart-prod
restart-prod: ## Restart prod stack
	down-prod up-prod

.PHONY: up-prod
up-prod: ## Start prod stack
	$(COMPOSE_PROD) up -d --build

.PHONY: down-prod
down-prod: ## Stop prod stack
	$(COMPOSE_PROD) down -v

.PHONY: logs-prod
logs-prod: ## Tail prod logs
	$(COMPOSE_PROD) logs -f --tail=200

.PHONY: migrate-prod
migrate-prod: ## Run Django migrations in prod
	$(COMPOSE_PROD) run --rm backend bash -lc "cd /app && python -m app.manage migrate"

.PHONY: bash-prod
bash-prod: ## Shell into backend container (prod)
	$(COMPOSE_PROD) exec backend bash

.PHONY: smoke-prod
smoke-prod: ## Prod smoketest (API + static + FE root)
	URL_ROOT := https://localhost
	CURL_FLAGS := -k
	smoke

.PHONY: be.wait-prod
be.wait-prod: ## Wait for backend health (prod)
	URL_ROOT := https://localhost
	CURL_FLAGS := -k
	be.wait

# ---- Django dev helpers ----
.PHONY: be.run
be.run: ## Run Django dev server (app.settings.dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage runserver 0.0.0.0:8000

.PHONY: migrate
migrate: ## Run Django migrations (dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage migrate

.PHONY: superuser
superuser: ## Create Django superuser (dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage createsuperuser

# ---- Setup / Hooks ----
.PHONY: setup
setup: ## Install Python/FE deps and pre-commit hooks
	be.setup fe.setup

.PHONY: be.setup
be.setup: ## Sync backend deps (uv) and install hooks
	cd $(PY_DIR) && uv sync --all-extras
	uv run pre-commit install

.PHONY: hooks
hooks: ## Install pre-commit hooks
	uv run pre-commit install

# ---- Quality gates ----
.PHONY: verify
verify: ## Run both backend and frontend verification
	be.verify fe.verify

.PHONY: be.verify
be.verify: ## Backend lint + typecheck + tests + coverage
	lint typecheck test coverage

.PHONY: preflight
preflight: ## Format + lint + typecheck + coverage + precommit
	format lint typecheck coverage precommit
	@echo "✅ Preflight complete."

.PHONY: lint
lint: ## Ruff lint (backend)
	cd $(PY_DIR) && uv run ruff check .

.PHONY: format
format: ## Apply Ruff fixes + format + Black (backend)
	cd $(PY_DIR) && uv run ruff check --fix .
	cd $(PY_DIR) && uv run ruff format .
	cd $(PY_DIR) && uv run black .

.PHONY: fmt-check
fmt-check: ## Check formatting (backend)
	cd $(PY_DIR) && uv run ruff format --check .
	cd $(PY_DIR) && uv run black --check .

.PHONY: typecheck
typecheck: ## MyPy type checking (backend)
	cd $(PY_DIR) && uv run mypy .

.PHONY: test
test: ## Pytest (backend)
	cd $(PY_DIR) && uv run pytest -q

.PHONY: coverage
coverage: ## Pytest coverage gate (backend)
	cd $(PY_DIR) && uv run pytest --cov=src --cov-report=term-missing

# ---- Pre-commit runner ----
.PHONY: precommit
precommit: ## Run pre-commit on all files
	uv run pre-commit run --all-files

# ---- Clean ----
.PHONY: clean
clean: ## Remove caches and build artifacts
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf $(PY_DIR)/.pytest_cache $(PY_DIR)/.mypy_cache

# ---- Frontend (Vite/React/TS) ----
.PHONY: fe.setup
fe.setup: ## Install frontend deps (pnpm)
	@corepack enable || true
	cd $(FRONTEND_DIR) && pnpm install

.PHONY: fe.run
fe.run: ## Run Vite dev server
	cd $(FRONTEND_DIR) && pnpm dev

.PHONY: fe.build
fe.build: ## Build frontend (Vite)
	cd $(FRONTEND_DIR) && pnpm build

.PHONY: fe.lint
fe.lint: ## ESLint (frontend)
	cd $(FRONTEND_DIR) && pnpm lint

.PHONY: fe.format
fe.format: ## Prettier write (frontend)
	cd $(FRONTEND_DIR) && pnpm format

.PHONY: fe.fmt-check
fe.fmt-check: ## Prettier check (frontend)
	cd $(FRONTEND_DIR) && pnpm fmt-check

.PHONY: fe.test
fe.test: ## Vitest (frontend)
	cd $(FRONTEND_DIR) && pnpm test

.PHONY: fe.typecheck
fe.typecheck: ## TypeScript typecheck (frontend)
	cd $(FRONTEND_DIR) && pnpm typecheck

.PHONY: fe.verify
fe.verify: ## Frontend lint + typecheck + tests + fmt-check
	cd $(FRONTEND_DIR) && pnpm verify
