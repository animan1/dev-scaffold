SCAFFOLD_ROOT := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
include $(SCAFFOLD_ROOT).scaffold-profile
UV_IMAGE ?= ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
SCAFFOLD_BACKUP_PROFILE ?= none
CI_BACKUP_PROFILES ?= immutable-backup

.PHONY: ci-profiles
ci-profiles:
	@printf '%s\n' "$(CI_PROFILES)"

.PHONY: ci-backup-profiles
ci-backup-profiles:
	@printf '%s\n' "$(CI_BACKUP_PROFILES)"

.PHONY: selected-profile
selected-profile:
	@printf '%s\n' "$(SCAFFOLD_PROFILE)"

ifeq ($(SCAFFOLD_PROFILE),react-vite)

SHELL := /bin/bash
COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml
FRONTEND_DIR := frontend
PROJECT_NAME ?= $(notdir $(CURDIR))
HOST_INGRESS ?= 0
MONITORING ?= 0
HOST_INGRESS_HOST ?= app.example.test
HOST_INGRESS_PORT ?= 18080
export HOST_INGRESS_PORT
PROD_COMPOSE_FILES := -f deploy/docker-compose.prod.yml
RELEASE_COMPOSE_FILES := -f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml
PROD_COMPOSE_PROFILES :=
ifeq ($(HOST_INGRESS),1)
PROD_COMPOSE_FILES += -f deploy/docker-compose.host-ingress.yml
RELEASE_COMPOSE_FILES += -f deploy/docker-compose.host-ingress.yml
endif
ifeq ($(MONITORING),1)
PROD_COMPOSE_FILES += -f deploy/docker-compose.external-monitoring.yml
RELEASE_COMPOSE_FILES += -f deploy/docker-compose.external-monitoring.yml
PROD_COMPOSE_PROFILES += --profile monitoring
endif
COMPOSE_PROD = docker compose $(PROD_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) --env-file deploy/.env.prod
COMPOSE_MONITOR = docker compose -f deploy/docker-compose.prod.yml \
	-f deploy/docker-compose.external-monitoring.yml --profile monitoring \
	--env-file deploy/.env.prod
RELEASE_COMPOSE_PROJECT ?= $(PROJECT_NAME)-release
RELEASE_IMAGE_PREFIX ?= local/$(PROJECT_NAME)
RELEASE_REVISION ?= $(shell git rev-parse HEAD)
RELEASE_BACKEND_TAG = $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG = $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
RELEASE_FILE ?= deploy/releases/$(RELEASE_REVISION).env
RELEASE_HTTP_PORT ?= 18080
RELEASE_HTTPS_PORT ?= 18443
COMPOSE_RELEASE = COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) docker compose \
	$(RELEASE_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) \
	--env-file deploy/.env.prod --env-file $(RELEASE_FILE)
COMPOSE_RELEASE_CI = COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) \
	PROD_ENV_FILE=../.tmp/release-ci.env docker compose \
	-f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml \
	-f deploy/docker-compose.release-ci.yml
COMPOSE_RELEASE_INGRESS_CI = COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) \
	PROD_ENV_FILE=../.tmp/release-ci.env docker compose \
	-f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml \
	-f deploy/docker-compose.host-ingress.yml

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
down: ## (Docker) Stop dev stack while preserving persistent volumes
	$(COMPOSE_DEV) down

.PHONY: reset
reset: ## (Docker, destructive) Stop dev stack and remove volumes; requires CONFIRM_RESET=1
	@if [ "$(CONFIRM_RESET)" != "1" ]; then \
		echo "Refusing to remove development volumes."; \
		echo "Re-run with: make reset CONFIRM_RESET=1"; \
		exit 1; \
	fi
	$(COMPOSE_DEV) down -v

.PHONY: restart
restart: ## (Docker) Restart dev stack
restart: down up

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
	$(COMPOSE_DEV) run --rm backend bash -lc 'REPO_DIR=/workspace make -f /workspace/Makefile PY_DIR=/app be.verify'

.PHONY: fe.ci
fe.ci: ## (Docker) Run frontend verify inside dev container
	$(COMPOSE_DEV) run --rm frontend sh -lc 'apk add --no-cache make >/dev/null 2>&1 || true; corepack enable; make -f /workspace/Makefile FRONTEND_DIR=/app fe.setup fe.verify'

.PHONY: smoke
smoke: ## Dev smoketest (API + static + FE root)
smoke: CURL_CMD = curl $(CURL_FLAGS) -fsS
smoke:
	$(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/static/smoketest.txt" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/" | grep -qi 'dev-scaffold\|id="root"'

.PHONY: ci
ci: ## (Docker) One-shot CI recipe (dev stack + FE/BE verify + smoke)
ci: up be.wait be.ci fe.ci smoke
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
	@if [ "$(HOST_INGRESS)" = "1" ]; then \
		echo "Host ingress owns TLS; skipping application certificate generation"; \
	elif [ ! -f deploy/nginx/certs/server.crt ] || [ ! -f deploy/nginx/certs/server.key ]; then \
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
	$(MAKE) smoke-prod HOST_INGRESS=$(HOST_INGRESS) \
		HOST_INGRESS_HOST=$(HOST_INGRESS_HOST) HOST_INGRESS_PORT=$(HOST_INGRESS_PORT)

.PHONY: restart-prod
restart-prod: ## Restart prod stack
restart-prod: down-prod up-prod

.PHONY: up-prod
up-prod: ## Start prod stack
	$(COMPOSE_PROD) up -d --build

.PHONY: down-prod
down-prod: ## Stop prod stack while preserving persistent volumes
	$(COMPOSE_PROD) down

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
ifeq ($(HOST_INGRESS),1)
smoke-prod: smoke-host-ingress
else
smoke-prod: URL_ROOT := https://localhost
smoke-prod: CURL_FLAGS := -k
smoke-prod: smoke
endif

.PHONY: ops.check-prod
ops.check-prod: ## Run operational checks once and report their state
	$(COMPOSE_PROD) run --rm backend python -m app.manage monitor_operational_integrity

.PHONY: ops.check-external-prod
ops.check-external-prod: ## Run checks once through the external monitoring profile
	$(COMPOSE_MONITOR) run --rm monitor python -m app.manage monitor_operational_integrity

.PHONY: ops.monitor-prod
ops.monitor-prod: ## Follow the unattended production monitor
	$(COMPOSE_MONITOR) logs -f monitor

.PHONY: smoke-host-ingress
smoke-host-ingress: ## Smoke-test the loopback origin as routed by a host ingress
smoke-host-ingress:
	@echo "Waiting for routed backend on http://127.0.0.1:$(HOST_INGRESS_PORT)/api/healthz ..."
	@for i in $$(seq 1 60); do \
		if curl -fsS -H 'Host: $(HOST_INGRESS_HOST)' \
			-H 'X-Forwarded-Proto: https' \
			'http://127.0.0.1:$(HOST_INGRESS_PORT)/api/healthz' >/dev/null; then \
			echo "Routed backend is up"; break; \
		fi; \
		if [ "$$i" = "60" ]; then \
			echo "Routed backend did not become ready in time"; exit 1; \
		fi; \
		sleep 1; \
	done
	curl -fsS -H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/api/healthz' >/dev/null
	curl -fsS -H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/static/smoketest.txt' >/dev/null
	curl -fsS -H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/' | grep -qi 'dev-scaffold\|id="root"'
	@code="$$(curl -sS -o /dev/null -w '%{http_code}' \
		-H 'Host: $(HOST_INGRESS_HOST)' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/api/healthz')"; \
	test "$$code" = "301" || { \
		echo "Origin must require the trusted ingress scheme header (got $$code)"; \
		exit 1; \
	}

# Optional immutable-release profile
.PHONY: build-release-images
build-release-images: ## Build SHA-tagged production images once
	docker build --target prod -t $(RELEASE_BACKEND_TAG) backend
	docker build -f deploy/nginx/Dockerfile -t $(RELEASE_WEB_TAG) .

.PHONY: prepare-release-ci
prepare-release-ci: ## Prepare isolated, non-secret release smoke-test configuration
	@mkdir -p .tmp deploy/nginx/certs
	@printf '%s\n' \
		'DJANGO_SECRET_KEY=release-ci-only' \
		'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,$(HOST_INGRESS_HOST)' \
		'DJANGO_CSRF_TRUSTED_ORIGINS=https://$(HOST_INGRESS_HOST)' \
		'POSTGRES_USER=app' \
		'POSTGRES_PASSWORD=release-ci-only' \
		'POSTGRES_DB=app' \
		'DATABASE_URL=postgresql://app:release-ci-only@db:5432/app' \
		> .tmp/release-ci.env
	@if [ ! -f deploy/nginx/certs/server.crt ] || [ ! -f deploy/nginx/certs/server.key ]; then \
		openssl req -x509 -nodes -newkey rsa:2048 \
			-keyout deploy/nginx/certs/server.key \
			-out deploy/nginx/certs/server.crt \
			-days 1 -subj '/CN=localhost' \
			-addext 'subjectAltName=DNS:localhost'; \
	fi

.PHONY: verify-release-images
verify-release-images: prepare-release-ci ## Verify and smoke-test the exact SHA-tagged images
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT) RELEASE_HTTPS_PORT=$(RELEASE_HTTPS_PORT) \
		$(COMPOSE_RELEASE_CI) config --quiet
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT) RELEASE_HTTPS_PORT=$(RELEASE_HTTPS_PORT) \
		$(COMPOSE_RELEASE_CI) up -d --no-build db backend
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_CI) exec -T backend python -m app.manage check
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_CI) exec -T backend python -m app.manage migrate
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_CI) exec -T backend python -m app.manage collectstatic --noinput
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT) RELEASE_HTTPS_PORT=$(RELEASE_HTTPS_PORT) \
		$(COMPOSE_RELEASE_CI) up -d --no-build
	@for i in $$(seq 1 60); do \
		if curl -kfsS https://localhost:$(RELEASE_HTTPS_PORT)/api/healthz >/dev/null; then \
			curl -kfsS https://localhost:$(RELEASE_HTTPS_PORT)/ >/dev/null; \
			echo 'Release images passed smoke tests'; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo 'Release images did not become ready'; exit 1

.PHONY: verify-host-ingress
verify-host-ingress: prepare-release-ci ## Verify exact images through the loopback ingress boundary
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) config --quiet
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) up -d --no-build db backend
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) exec -T backend python -m app.manage check
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) exec -T backend python -m app.manage migrate
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) exec -T backend python -m app.manage collectstatic --noinput
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		$(COMPOSE_RELEASE_INGRESS_CI) up -d --no-build
	$(MAKE) smoke-host-ingress

.PHONY: verify-monitoring-config
verify-monitoring-config: prepare-release-ci ## Validate the optional monitoring Compose profile
	PROD_ENV_FILE=../.tmp/release-ci.env docker compose \
		-f deploy/docker-compose.prod.yml \
		-f deploy/docker-compose.external-monitoring.yml \
		--profile monitoring config --quiet

.PHONY: down-release-ci
down-release-ci: ## Stop the isolated immutable-release test stack
	@if [ -f .tmp/release-ci.env ]; then \
		RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT) RELEASE_HTTPS_PORT=$(RELEASE_HTTPS_PORT) \
		$(COMPOSE_RELEASE_CI) down -v --remove-orphans; \
	fi

.PHONY: logs-release-ci
logs-release-ci: ## Show logs from the isolated immutable-release test stack
	RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
		RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT) RELEASE_HTTPS_PORT=$(RELEASE_HTTPS_PORT) \
		$(COMPOSE_RELEASE_CI) logs --tail=200

.PHONY: push-release-images
push-release-images: ## Push verified images and record their immutable digests
	docker push $(RELEASE_BACKEND_TAG)
	docker push $(RELEASE_WEB_TAG)
	@mkdir -p $(dir $(RELEASE_FILE))
	@backend="$$(docker image inspect --format='{{index .RepoDigests 0}}' $(RELEASE_BACKEND_TAG))"; \
	web="$$(docker image inspect --format='{{index .RepoDigests 0}}' $(RELEASE_WEB_TAG))"; \
	test -n "$$backend"; test -n "$$web"; \
	printf 'RELEASE_REVISION=%s\nRELEASE_IMAGE_PREFIX=%s\nRELEASE_BACKEND_IMAGE=%s\nRELEASE_WEB_IMAGE=%s\n' \
		'$(RELEASE_REVISION)' '$(RELEASE_IMAGE_PREFIX)' "$$backend" "$$web" > $(RELEASE_FILE); \
	echo "Recorded $(RELEASE_FILE)"

.PHONY: deploy-release
deploy-release: ## Pull and deploy the digest-pinned images in RELEASE_FILE
	@test -f $(RELEASE_FILE) || { echo 'Set RELEASE_FILE to a recorded release manifest'; exit 1; }
	$(COMPOSE_RELEASE) config --quiet
	$(COMPOSE_RELEASE) pull backend nginx
	$(COMPOSE_RELEASE) up -d --no-build db backend
	$(COMPOSE_RELEASE) exec -T backend python -m app.manage migrate
	$(COMPOSE_RELEASE) exec -T backend python -m app.manage collectstatic --noinput
	$(COMPOSE_RELEASE) up -d --no-build

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded RELEASE_FILE
rollback-release: deploy-release

.PHONY: be.wait-prod
be.wait-prod: ## Wait for backend health (prod)
be.wait-prod: URL_ROOT := https://localhost
be.wait-prod: CURL_FLAGS := -k
be.wait-prod: be.wait

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

.PHONY: shell
shell: ## Run Django shell (dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage shell

# ---- Setup / Hooks ----
.PHONY: setup
setup: ## Install Python/FE deps and pre-commit hooks
setup: be.setup fe.setup

.PHONY: be.setup
be.setup: ## Sync backend deps (uv) and install hooks
be.setup: be.sync hooks

.PHONY: be.sync
be.sync:
	cd $(PY_DIR) && uv sync --all-extras

.PHONY: deps.lock
deps.lock: ## Update the selected profile's Python lockfile with pinned Dockerized uv
	docker run --rm --user "$$(id -u):$$(id -g)" \
		--env UV_CACHE_DIR=/tmp/uv-cache \
		--tmpfs /tmp:rw,mode=1777 \
		--volume "$(CURDIR)/$(PY_DIR):/workspace" \
		--workdir /workspace --entrypoint /usr/local/bin/uv $(UV_IMAGE) lock

.PHONY: hooks
hooks: ## Install pre-commit hooks
	cd $(PY_DIR) && uv run pre-commit install

# ---- Quality gates ----
.PHONY: verify
verify: ## Run both backend and frontend verification
verify: be.verify fe.verify

.PHONY: be.verify
be.verify: ## Backend format check + lint + typecheck + tests + coverage
be.verify: fmt-check lint typecheck coverage

.PHONY: preflight
preflight: ## Format + lint + typecheck + coverage + precommit
preflight: format lint typecheck coverage precommit
	@echo "✅ Preflight complete."

.PHONY: no-any
no-any: ## Reject explicit typing.Any in backend Python
	cd $(PY_DIR) && uv run python scripts/check_no_any.py

.PHONY: lint
lint: ## Ruff lint (backend)
lint: no-any
	cd $(PY_DIR) && uv run ruff check .

.PHONY: format
format: ## Apply Ruff fixes and formatting (backend)
	cd $(PY_DIR) && uv run ruff check --fix .
	cd $(PY_DIR) && uv run ruff format .

.PHONY: fmt-check
fmt-check: ## Check formatting (backend)
	cd $(PY_DIR) && uv run ruff format --check .

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
precommit: fe.setup
	cd $(PY_DIR) && uv run --extra dev pre-commit run --all-files

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

else
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_PROFILE)/profile.mk
endif

ifneq ($(SCAFFOLD_BACKUP_PROFILE),none)
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_BACKUP_PROFILE)/profile.mk
endif
