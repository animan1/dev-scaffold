SCAFFOLD_ROOT := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
include $(SCAFFOLD_ROOT).scaffold-profile
UV_IMAGE ?= ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
SCAFFOLD_BACKUP_PROFILE ?= immutable-backup
CI_BACKUP_PROFILES ?= immutable-backup
PROD_ENV_FILE ?= deploy/.env.prod
PROD_APPLICATION_SERVICE ?= backend
PROD_CONFIG = $(COMPOSE_PROD)
HOST_INGRESS ?= 1
# Capture tenant/operator input before the smoke-test placeholder default.
PREPARE_PUBLIC_HOST := $(HOST_INGRESS_HOST)

.PHONY: profiles
profiles:
	@python3 scripts/profile_metadata.py --application '$(SCAFFOLD_PROFILE)' \
		--backup '$(SCAFFOLD_BACKUP_PROFILE)' --applications $(CI_PROFILES) \
		--backups $(CI_BACKUP_PROFILES)

ifeq ($(SCAFFOLD_PROFILE),react-vite)

SHELL := /bin/bash
COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml
FRONTEND_DIR := frontend
PROJECT_NAME ?= $(notdir $(CURDIR))
COMPOSE_PROJECT_NAME ?= $(PROJECT_NAME)
export COMPOSE_PROJECT_NAME
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
COMPOSE_PROD = PROD_ENV_FILE=$(abspath $(PROD_ENV_FILE)) docker compose $(PROD_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) --env-file $(PROD_ENV_FILE)
COMPOSE_MONITOR = docker compose -f deploy/docker-compose.prod.yml \
	-f deploy/docker-compose.external-monitoring.yml --profile monitoring \
	--env-file deploy/.env.prod
RELEASE_COMPOSE_PROJECT ?= $(PROJECT_NAME)-release
RELEASE_IMAGE_PREFIX ?= local/$(PROJECT_NAME)
RELEASE_REVISION ?= $(shell git rev-parse HEAD)
RELEASE_BACKEND_TAG = $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG = $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
RELEASE_FILE ?= deploy/releases/$(RELEASE_REVISION).env
RELEASE_PLATFORM ?=
RELEASE_PULL ?= $(COMPOSE_RELEASE) pull backend nginx
RELEASE_HTTP_PORT ?= 18080
RELEASE_HTTPS_PORT ?= 18443
COMPOSE_RELEASE = PROD_ENV_FILE=$(abspath $(PROD_ENV_FILE)) COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) docker compose \
	$(RELEASE_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) \
	--env-file $(PROD_ENV_FILE) --env-file $(RELEASE_FILE)
RUN_RELEASE = $(COMPOSE_RELEASE) run --rm --no-deps backend
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
FEATURED := 'up down restart logs check smoke up-prod smoke-prod help help-verbose'

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help.
	@FEATURED=$(FEATURED); \
	ALL="$$(grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST))"; \
	printf "Make targets:\n\n"; \
	printf "  \033[1mFeatured\033[0m\n"; \
	for t in $$FEATURED; do \
	  echo "$$ALL" | grep -E "^$${t}:.*?## " | \
	    awk 'BEGIN {FS=":.*?## "} {printf "    \033[36m%-24s\033[0m %s\n", $$1, $$2}'; \
	done

.PHONY: help-verbose
help-verbose: ## Show full help (featured + everything else)
	@FEATURED=$(FEATURED); \
	ALL="$$(grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST))"; \
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
	$(COMPOSE_DEV) logs || true; \
	exit 1

.PHONY: smoke
smoke: ## Dev smoketest (API + static + FE root)
smoke: CURL_CMD = curl $(CURL_FLAGS) -fsS
smoke:
	$(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/static/smoketest.txt" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/" | grep -qi 'dev-scaffold\|id="root"'

.PHONY: ci
ci: ## (Docker) One-shot CI recipe (dev stack + FE/BE verify + smoke)
ci: up be.wait check smoke
	@echo "CI checks passed"

# Prod
.PHONY: bootstrap-prod
bootstrap-prod: prepare-prod ## Prepare inputs and start the production stack
	# Bring up stack, run migrations, and smoke test
	$(MAKE) up-prod
	$(COMPOSE_PROD) run --rm backend bash -lc "cd /app && python -m app.manage migrate"
	$(MAKE) smoke-prod HOST_INGRESS=$(HOST_INGRESS) \
		HOST_INGRESS_HOST=$(HOST_INGRESS_HOST) HOST_INGRESS_PORT=$(HOST_INGRESS_PORT)

.PHONY: restart-prod
restart-prod: ## Restart prod stack
restart-prod: down-prod up-prod

.PHONY: up-prod
up-prod: ## Start prod stack
	$(COMPOSE_PROD) up -d --build
	$(COMPOSE_PROD) up -d --no-deps --force-recreate nginx

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
	@location="$$(curl -fsS -D - -o /dev/null \
		-H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/admin' \
		| awk 'tolower($$1) == "location:" {sub(/\r$$/, "", $$2); print $$2}')"; \
	test "$$location" = '/admin/' || { \
		echo "Django admin slash redirect was not routed (got $$location)"; \
		exit 1; \
	}
	@location="$$(curl -fsS -D - -o /dev/null \
		-H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(HOST_INGRESS_PORT)/admin/' \
		| awk 'tolower($$1) == "location:" {sub(/\r$$/, "", $$2); print $$2}')"; \
	case "$$location" in \
		/admin/login/*) ;; \
		*) echo "Django admin login was not routed (got $$location)"; exit 1 ;; \
	esac
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
	docker build --target prod -t $(RELEASE_BACKEND_TAG) $(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) \
		--label org.opencontainers.image.revision=$(RELEASE_REVISION) backend
	docker build -f deploy/nginx/Dockerfile -t $(RELEASE_WEB_TAG) $(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) \
		--label org.opencontainers.image.revision=$(RELEASE_REVISION) .
	python3 scripts/record_local_release.py --revision $(RELEASE_REVISION) \
		--profile $(SCAFFOLD_PROFILE) --prefix $(RELEASE_IMAGE_PREFIX) \
		--backend $(RELEASE_BACKEND_TAG) --web $(RELEASE_WEB_TAG) \
		$(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) --output $(RELEASE_FILE)

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

.PHONY: initialize-release
initialize-release: ## Initialize the database/static volume with the recorded backend image
	@test -f $(RELEASE_FILE) || { echo 'Set RELEASE_FILE to a recorded release manifest'; exit 1; }
	$(COMPOSE_RELEASE) config --quiet
	$(RELEASE_PULL)
	$(COMPOSE_RELEASE) up -d --no-build db
	$(RUN_RELEASE) python -m app.manage check --deploy
	$(RUN_RELEASE) python -m app.manage migrate
	$(RUN_RELEASE) python -m app.manage collectstatic --noinput

.PHONY: deploy-release
deploy-release: initialize-release ## Deploy the recorded images through normal production initialization
	$(COMPOSE_RELEASE) up -d --no-build
	$(COMPOSE_RELEASE) up -d --no-deps --force-recreate nginx

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded RELEASE_FILE
rollback-release: deploy-release

.PHONY: be.wait-prod
be.wait-prod: ## Wait for backend health (prod)
be.wait-prod: URL_ROOT := https://localhost
be.wait-prod: CURL_FLAGS := -k
be.wait-prod: be.wait

# ---- Django dev helpers ----
DJANGO_DEV_MANAGE ?= cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage

.PHONY: be.run
be.run: ## Run Django dev server (app.settings.dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage runserver 0.0.0.0:8000

# ---- Setup / Hooks ----
.PHONY: setup
setup: ## Prepare Docker development dependencies from committed lockfiles
	$(COMPOSE_DEV) build backend
	@mkdir -p frontend/node_modules
	$(COMPOSE_DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" \
		--env HOME=/tmp --env COREPACK_HOME=/tmp/corepack \
		--volume "$(CURDIR)/frontend/node_modules:/app/node_modules" \
		frontend corepack pnpm install --frozen-lockfile

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

.PHONY: verify precommit
verify: check ## Run source checks (legacy aggregate)
precommit: fe.setup ## Run configured pre-commit hooks
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

else
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_PROFILE)/profile.mk
endif

# Source gates reuse the tooling built by setup; never start a Compose stack.
SOURCE_IMAGE ?= $(if $(filter react-vite,$(SCAFFOLD_PROFILE)),$(COMPOSE_PROJECT_NAME)-backend,$(PROJECT_NAME)-app)
SOURCE_VENV := $(if $(filter react-vite,$(SCAFFOLD_PROFILE)),/app/.venv,/opt/venv)
SOURCE_CONFIG := /workspace/$(if $(filter react-vite,$(SCAFFOLD_PROFILE)),backend,profiles/server-rendered-django)/pyproject.toml
SOURCE_GIT_DIR = $(shell git rev-parse --path-format=absolute --git-common-dir)
DIFF_BASE ?= origin/main
CHANGED_COVERAGE_MIN ?= 90
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
SOURCE_RUN = docker run --rm --pull=never --network none --read-only --tmpfs /tmp:rw,exec,mode=1777 \
	--user "$(HOST_UID):$(HOST_GID)" --volume "$(CURDIR):/workspace" \
	--volume "$(SOURCE_GIT_DIR):$(SOURCE_GIT_DIR):ro" --workdir /workspace/backend \
	--env HOME=/tmp --env PYTHONPATH=/workspace/backend/src \
	--env DJANGO_SETTINGS_MODULE=app.project.settings.dev --env DATABASE_URL=sqlite://:memory: \
	--env DJANGO_SECRET_KEY=source-check-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ \
	--env DJANGO_ALLOWED_HOSTS=localhost --env DJANGO_EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend \
	--env RUFF_CACHE_DIR=/tmp/ruff --env MYPY_CACHE_DIR=/tmp/mypy --env COVERAGE_FILE=/tmp/.coverage \
	--env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/workspace \
	--env PYTEST_ADDOPTS
SOURCE_PYTHON = $(SOURCE_RUN) $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/python
SOURCE_FRONTEND = $(SOURCE_RUN) --workdir /workspace/frontend --entrypoint /bin/sh node:20
SOURCE_FE_COVERAGE_ARGS :=
ifeq ($(COVERAGE),1)
SOURCE_FE_COVERAGE_ARGS := --coverage --coverage.all=true \
	'--coverage.include=src/**/*.{ts,tsx}' '--coverage.exclude=src/**/__tests__/**' \
	'--coverage.exclude=src/**/*.{test,spec}.{ts,tsx}' --coverage.exclude=src/setupTests.ts \
	'--coverage.exclude=src/**/*.d.ts'
endif

.PHONY: check format-check lint typecheck migrations-check framework-check test format
check: ## Run all source gates without deployment or application startup
	$(MAKE) format-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) migrations-check
	$(MAKE) framework-check
	$(MAKE) test COVERAGE=1

format-check: ## Check formatting across the selected application's components
	$(SOURCE_PYTHON) -m ruff format --config $(SOURCE_CONFIG) --check .
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_FRONTEND) node_modules/.bin/prettier --check .
endif

lint: ## Lint the selected application's components without fixes
	$(SOURCE_PYTHON) scripts/check_no_any.py
	$(SOURCE_PYTHON) -m ruff check --config $(SOURCE_CONFIG) .
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_FRONTEND) node_modules/.bin/eslint . --ext .ts,.tsx
endif

typecheck: ## Type-check the selected application's components
	$(SOURCE_PYTHON) -m mypy --config-file $(SOURCE_CONFIG) .
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_FRONTEND) node_modules/.bin/tsc -b --pretty false --noEmit
endif

migrations-check: ## Check migration source consistency without changing schema
	$(SOURCE_PYTHON) -m app.manage makemigrations --check --dry-run

framework-check: ## Check framework configuration without a running database
	$(SOURCE_RUN) --env DJANGO_SETTINGS_MODULE=app.project.settings.prod $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/python -m app.manage check --deploy

test: ## Run source tests once; COVERAGE=1 also gates total and changed-line coverage
	$(SOURCE_PYTHON) -m pytest -c $(SOURCE_CONFIG) -q -p no:cacheprovider \
		$(if $(filter 1,$(COVERAGE)),--cov=src --cov-config=$(SOURCE_CONFIG) --cov-report=term-missing --cov-report=xml:coverage.xml)
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_FRONTEND) node_modules/.bin/vitest run $(SOURCE_FE_COVERAGE_ARGS)
endif
ifeq ($(COVERAGE),1)
	$(SOURCE_RUN) $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/diff-cover coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=$(CHANGED_COVERAGE_MIN)
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_RUN) --workdir /workspace/frontend $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/diff-cover coverage/lcov.info --compare-branch=$(DIFF_BASE) --fail-under=$(CHANGED_COVERAGE_MIN)
endif
endif

format: ## Apply formatting and safe fixes across the selected application's components
	$(SOURCE_PYTHON) -m ruff check --config $(SOURCE_CONFIG) --fix .
	$(SOURCE_PYTHON) -m ruff format --config $(SOURCE_CONFIG) .
ifeq ($(SCAFFOLD_PROFILE),react-vite)
	$(SOURCE_FRONTEND) node_modules/.bin/prettier --write .
endif

LOCAL_TLS ?= 0
up-prod: prepare-prod

.PHONY: prepare-prod
prepare-prod: ## Prepare and validate private production inputs without starting services
	@PROD_ENV_FILE="$(PROD_ENV_FILE)" bash "$(SCAFFOLD_ROOT)deploy/setup-env.sh"
	@set -o pipefail; prepare_interactive=0; \
		if [ -t 0 ]; then prepare_interactive=1; fi; \
		if [ "$(SCAFFOLD_BACKUP_PROFILE)" != "none" ]; then \
			PREPARE_INTERACTIVE=$$prepare_interactive PROD_ENV_FILE="$(PROD_ENV_FILE)" \
			python3 "$(SCAFFOLD_ROOT)scripts/validate_prod_config.py" --backup-path || exit 1; \
		fi; \
		$(PROD_CONFIG) config --format json 2>/dev/null | \
		PREPARE_INTERACTIVE=$$prepare_interactive PROD_ENV_FILE="$(PROD_ENV_FILE)" HOST_INGRESS="$(HOST_INGRESS)" HOST_INGRESS_HOST="$(PREPARE_PUBLIC_HOST)" \
		python3 "$(SCAFFOLD_ROOT)scripts/validate_prod_config.py" "$(PROD_APPLICATION_SERVICE)"
	# Self-signed certificate renewal is explicitly local-only.
	@if [ "$(HOST_INGRESS)" = "1" ]; then \
		echo "Host ingress owns TLS; skipping application certificate generation"; \
	elif [ "$(LOCAL_TLS)" = "1" ]; then \
		if [ ! -f deploy/nginx/certs/server.key ] || \
			! openssl x509 -in deploy/nginx/certs/server.crt -noout -checkend 86400 >/dev/null 2>&1; then \
			umask 077; mkdir -p deploy/nginx/certs; \
			openssl req -x509 -nodes -newkey rsa:2048 \
				-keyout deploy/nginx/certs/server.key -out deploy/nginx/certs/server.crt \
				-days 365 -subj '/CN=localhost' \
				-addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' || exit 1; \
		fi; \
		echo 'Local TLS certificate ready for localhost and 127.0.0.1'; \
	else \
		test -f deploy/nginx/certs/server.key && \
			openssl x509 -in deploy/nginx/certs/server.crt -noout -checkend 0 >/dev/null || \
			{ echo 'Install valid production TLS inputs, or use LOCAL_TLS=1 for the laptop dummy'; exit 1; }; \
	fi

.PHONY: migrations migrate superuser shell
migrations: ## Create migrations for intentional model changes
	$(DJANGO_DEV_MANAGE) makemigrations

migrate: ## Apply development database migrations
	$(DJANGO_DEV_MANAGE) migrate

superuser: migrate ## Migrate, then create an interactive Django superuser (development only)
	$(DJANGO_DEV_MANAGE) createsuperuser

shell: ## Open the Django shell (development only)
	$(DJANGO_DEV_MANAGE) shell

ifneq ($(SCAFFOLD_BACKUP_PROFILE),none)
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_BACKUP_PROFILE)/profile.mk
SELECTED_OPTIONAL_PROFILE_MAKEFILES := $(SELECTED_OPTIONAL_PROFILE_MAKEFILES) $(lastword $(MAKEFILE_LIST))
endif

include $(SCAFFOLD_ROOT)profiles/release-transport.mk
