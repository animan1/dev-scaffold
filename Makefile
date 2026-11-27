SCAFFOLD_ROOT := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
include $(SCAFFOLD_ROOT).scaffold-profile
SHELL := /bin/bash
PROJECT_NAME ?= $(notdir $(CURDIR))
DIFF_BASE ?= origin/main
DEV_COMPOSE_PROJECT ?= $(PROJECT_NAME)-dev
PROD_COMPOSE_PROJECT ?= $(PROJECT_NAME)-prod
EPHEMERAL_COMPOSE_PROJECT ?= $(PROJECT_NAME)-ephemeral
RELEASE_IMAGE_PREFIX ?= local/$(PROJECT_NAME)
RELEASE_REVISION ?= $(shell git rev-parse HEAD)
RELEASE_BACKEND_TAG ?= $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG ?= $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
RELEASE_FILE ?= deploy/releases/$(RELEASE_REVISION).env
RELEASE_PLATFORM ?=
RELEASE_HTTP_PORT ?= 18080
PROD_HTTPS_PORT ?= 18443
export PROD_HTTPS_PORT
UV_IMAGE ?= ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
SCAFFOLD_BACKUP_PROFILE ?= immutable-backup
CI_BACKUP_PROFILES ?= immutable-backup
PROD_ENV_FILE ?= deploy/.env.prod
PROD_APPLICATION_SERVICE ?= backend
COMPOSE_PROD = COMPOSE_PROJECT_NAME=$(PROD_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(abspath $(PROD_ENV_FILE)) docker compose --project-directory $(PROD_COMPOSE_PROJECT_DIR) \
	$(PROD_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) --env-file $(PROD_ENV_FILE) --env-file $(RELEASE_FILE)
RELEASE_PULL ?= $(COMPOSE_PROD) pull $(PROD_APPLICATION_SERVICE) $(PROD_WEB_SERVICE)
RUN_PROD = $(COMPOSE_PROD) run --rm --no-deps $(PROD_APPLICATION_SERVICE)
PROD_CONFIG = $(COMPOSE_PROD)
HOST_INGRESS ?= 1
# Capture tenant/operator input before the smoke-test placeholder default.
PREPARE_PUBLIC_HOST := $(HOST_INGRESS_HOST)
HOST_INGRESS_HOST ?= 127.0.0.1
APP_HOST ?= 127.0.0.1
DEV_HTTP_PORT ?= 8080
export DEV_HTTP_PORT
DEV_URL_ROOT ?= http://127.0.0.1:$(DEV_HTTP_PORT)
CURL_FLAGS ?= -H 'Host: $(APP_HOST)'
HOST_INGRESS_PORT ?= $(RELEASE_HTTP_PORT)
export HOST_INGRESS_PORT
ifeq ($(HOST_INGRESS),1)
PROD_HTTPS_URL ?= https://$(HOST_INGRESS_HOST)
else
PROD_HTTPS_URL ?= https://127.0.0.1:$(PROD_HTTPS_PORT)
PROD_HTTPS_CA ?= deploy/nginx/certs/server.crt
endif
PROD_HTTPS_CURL_FLAGS = --proto '=https' --proto-redir '=https' -L $(if $(PROD_HTTPS_CA),--cacert '$(PROD_HTTPS_CA)')

.PHONY: profiles
profiles:
	@python3 scripts/profile_metadata.py --application '$(SCAFFOLD_PROFILE)' \
		--backup '$(SCAFFOLD_BACKUP_PROFILE)' --applications $(CI_PROFILES) \
		--backups $(CI_BACKUP_PROFILES)

HELP_FILES = $(MAKEFILE_LIST)
EPHEMERAL_ENV_FILE ?= $(CURDIR)/.tmp/ephemeral.env
UV_RUN = uv run --project $(CONTAINER_PYTHON_PROJECT_DIR)
PRECOMMIT_CONFIG := .pre-commit-config.yaml
GIT_COMMON_DIR ?= $(shell git rev-parse --path-format=absolute --git-common-dir)
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
COMPOSE_DEV = COMPOSE_PROJECT_NAME=$(DEV_COMPOSE_PROJECT) APP_HOST=$(APP_HOST) DEV_HTTP_PORT=$(DEV_HTTP_PORT) \
	GIT_COMMON_DIR="$(GIT_COMMON_DIR)" HOST_UID=$(HOST_UID) HOST_GID=$(HOST_GID) \
	docker compose --project-directory $(DEV_COMPOSE_PROJECT_DIR) -f $(DEV_COMPOSE_FILE)
DEV_RUN_BACKEND_MAKE = $(COMPOSE_DEV) run --rm --workdir /workspace/backend --env REPO_DIR=/workspace $(1) \
	$(DEV_APPLICATION_SERVICE) make --no-print-directory -f /workspace/Makefile SCAFFOLD_PROFILE=$(SCAFFOLD_PROFILE)
DJANGO_DEV_MANAGE = $(COMPOSE_DEV) run --rm$(if $(1), $(1)) --workdir $(CONTAINER_PYTHON_PROJECT_DIR) \
	$(DEV_APPLICATION_SERVICE) $(UV_RUN) python -m app.manage

ifeq ($(SCAFFOLD_PROFILE),react-vite)

DEV_COMPOSE_FILE := deploy/docker-compose.dev.yml
DEV_COMPOSE_PROJECT_DIR := deploy
PROD_COMPOSE_PROJECT_DIR := deploy
DEV_APPLICATION_SERVICE := backend
CONTAINER_PYTHON_PROJECT_DIR := /app
FRONTEND_DIR := frontend
MONITORING ?= 0
EPHEMERAL_HTTPS_PORT ?= 18443
PROD_COMPOSE_FILES := -f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml
PROD_COMPOSE_PROFILES :=
ifeq ($(HOST_INGRESS),1)
PROD_COMPOSE_FILES += -f deploy/docker-compose.host-ingress.yml
endif
ifeq ($(MONITORING),1)
PROD_COMPOSE_FILES += -f deploy/docker-compose.external-monitoring.yml
PROD_COMPOSE_PROFILES += --profile monitoring
endif
# Resolve the same topology before a release manifest exists.
PROD_CONFIG = RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) \
	COMPOSE_PROJECT_NAME=$(PROD_COMPOSE_PROJECT) PROD_ENV_FILE=$(abspath $(PROD_ENV_FILE)) \
	docker compose $(PROD_COMPOSE_FILES) $(PROD_COMPOSE_PROFILES) --env-file $(PROD_ENV_FILE)
COMPOSE_MONITOR = $(COMPOSE_PROD) -f deploy/docker-compose.external-monitoring.yml --profile monitoring
PROD_WEB_SERVICE := nginx
COMPOSE_EPHEMERAL = COMPOSE_PROJECT_NAME=$(EPHEMERAL_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(EPHEMERAL_ENV_FILE) EPHEMERAL_HTTPS_PORT=$(EPHEMERAL_HTTPS_PORT) docker compose \
	-f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml \
	-f deploy/docker-compose.ephemeral.yml $(EPHEMERAL_COMPOSE_FILES) --env-file $(EPHEMERAL_ENV_FILE) --env-file $(RELEASE_FILE)
COMPOSE_EPHEMERAL_INGRESS = COMPOSE_PROJECT_NAME=$(EPHEMERAL_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(EPHEMERAL_ENV_FILE) docker compose \
	-f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml \
	-f deploy/docker-compose.host-ingress.yml --env-file $(EPHEMERAL_ENV_FILE) --env-file $(RELEASE_FILE)

# ---- Paths ----
PY_DIR := backend
PYTHON_PROJECT_DIR := backend
SMOKE_HOME_PATTERN := dev-scaffold\|id="root"

.PHONY: be.bash
be.bash: ## (Docker) Shell into backend container
	$(COMPOSE_DEV) exec backend bash

# Prod
.PHONY: bootstrap-prod
bootstrap-prod: prepare-prod ## Prepare inputs and start the production stack
	# Bring up stack, run migrations, and smoke test
	$(MAKE) up-prod
	$(MAKE) smoke-prod HOST_INGRESS=$(HOST_INGRESS) \
		HOST_INGRESS_HOST=$(HOST_INGRESS_HOST) HOST_INGRESS_PORT=$(HOST_INGRESS_PORT)

.PHONY: migrate-prod
migrate-prod: ## Run Django migrations in prod
	$(COMPOSE_PROD) run --rm backend bash -lc "cd /app && python -m app.manage migrate"

.PHONY: bash-prod
bash-prod: ## Shell into backend container (prod)
	$(COMPOSE_PROD) exec backend bash

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
smoke-host-ingress: COMPOSE = $(COMPOSE_PROD)
smoke-host-ingress: URL_ROOT = http://127.0.0.1:$(HOST_INGRESS_PORT)
smoke-host-ingress: CURL_CMD = curl -fsS -L -H 'Host: $(HOST_INGRESS_HOST)' -H 'X-Forwarded-Proto: https'
smoke-host-ingress: smoke
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

.PHONY: verify-ephemeral
verify-ephemeral: COMPOSE = $(COMPOSE_EPHEMERAL)
verify-ephemeral: URL_ROOT = https://127.0.0.1:$(EPHEMERAL_HTTPS_PORT)
verify-ephemeral: CURL_CMD = curl -fsS --cacert "$(EPHEMERAL_CERTS_PATH)/server.crt"
verify-ephemeral: smoke ## Verify the running recorded release in ephemeral

.PHONY: verify-release-images
verify-release-images: up-ephemeral ## Verify and smoke-test the exact recorded images
	$(MAKE) verify-ephemeral

.PHONY: verify-host-ingress
verify-host-ingress: prepare-ephemeral ## Verify exact images through the loopback ingress boundary
	$(COMPOSE_EPHEMERAL_INGRESS) config --quiet
	$(COMPOSE_EPHEMERAL_INGRESS) up -d --no-build db backend
	$(COMPOSE_EPHEMERAL_INGRESS) exec -T backend python -m app.manage check
	$(COMPOSE_EPHEMERAL_INGRESS) exec -T backend python -m app.manage migrate
	$(COMPOSE_EPHEMERAL_INGRESS) exec -T backend python -m app.manage collectstatic --noinput --clear
	$(COMPOSE_EPHEMERAL_INGRESS) up -d --no-build
	$(MAKE) smoke-host-ingress 'COMPOSE=$(COMPOSE_EPHEMERAL_INGRESS)'

.PHONY: verify-monitoring-config
verify-monitoring-config: prepare-ephemeral ## Validate the optional monitoring Compose profile
	$(COMPOSE_EPHEMERAL) -f deploy/docker-compose.external-monitoring.yml --profile monitoring config --quiet

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
deploy-release: initialize-release ## Deploy the recorded images through normal production initialization
	$(COMPOSE_PROD) up -d --no-build
	$(COMPOSE_PROD) up -d --no-deps --force-recreate nginx

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded RELEASE_FILE
rollback-release: deploy-release

# ---- Django dev helpers ----

.PHONY: be.run
be.run: ## Run Django dev server (app.settings.dev)
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage runserver 0.0.0.0:8000

# ---- Setup / Hooks ----
.PHONY: hooks
hooks: ## Install pre-commit hooks
	cd $(PY_DIR) && uv run pre-commit install

# ---- Quality gates ----
.PHONY: preflight
preflight: ## Format + lint + typecheck + coverage + precommit
preflight: format lint typecheck coverage precommit
	@echo "✅ Preflight complete."


# ---- Clean ----
.PHONY: clean
clean: ## Remove caches and build artifacts
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf $(PY_DIR)/.pytest_cache $(PY_DIR)/.mypy_cache

# ---- Frontend (Vite/React/TS) ----
.PHONY: fe.setup fe.setup-in-container
fe.setup: ## Install locked frontend dependencies in Docker
	@mkdir -p "$(FRONTEND_DIR)/node_modules"
	$(COMPOSE_DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" \
		--env HOME=/tmp --env COREPACK_HOME=/tmp/corepack \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend make -f /workspace/Makefile SCAFFOLD_PROFILE=react-vite fe.setup-in-container

fe.setup-in-container:
	corepack pnpm install --frozen-lockfile

.PHONY: fe.run
fe.run: ## Run Vite dev server
	cd $(FRONTEND_DIR) && pnpm dev

.PHONY: fe.build
fe.build: ## Build frontend (Vite)
	cd $(FRONTEND_DIR) && pnpm build

.PHONY: fe.lint
fe.lint: ## ESLint (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm lint --max-warnings 0

.PHONY: fe.format
fe.format: ## Prettier write (frontend)
	cd $(FRONTEND_DIR) && pnpm format

.PHONY: fe.fmt-check
fe.fmt-check: ## Prettier check (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm fmt-check

.PHONY: fe.test fe.test-in-container
fe.test: ## Run frontend tests in Docker
	$(COMPOSE_DEV) run --rm --no-deps frontend sh -ec 'corepack pnpm install --frozen-lockfile && \
		make --no-print-directory -f /workspace/Makefile SCAFFOLD_PROFILE=react-vite \
		FRONTEND_DIR=/app COVERAGE=$(COVERAGE) fe.test-in-container'

FE_COVERAGE_ARGS :=
ifeq ($(COVERAGE),1)
FE_COVERAGE_ARGS := --coverage
endif
fe.test-in-container:
	cd $(FRONTEND_DIR) && corepack pnpm run test $(FE_COVERAGE_ARGS)

.PHONY: fe.typecheck
fe.typecheck: ## TypeScript typecheck (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm typecheck

.PHONY: fe.coverage
fe.coverage:
	$(COMPOSE_DEV) run --rm --no-deps --workdir /workspace/frontend \
		frontend make --no-print-directory -f /workspace/Makefile SCAFFOLD_PROFILE=react-vite \
		FRONTEND_DIR=/workspace/frontend COVERAGE=1 fe.setup-in-container fe.test-in-container
	$(COMPOSE_DEV) run --rm --no-deps --workdir /workspace/frontend \
		--volume "$(GIT_COMMON_DIR):$(GIT_COMMON_DIR):ro" \
		backend $(CONTAINER_PYTHON_PROJECT_DIR)/.venv/bin/diff-cover coverage/lcov.info \
		--config-file /workspace/backend/pyproject.toml $(if $(DIFF_BASE),--compare-branch=$(DIFF_BASE))

else
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_PROFILE)/profile.mk
endif

.DEFAULT_GOAL := help
.PHONY: setup
setup: ## Prepare Docker development dependencies from committed lockfiles
	$(COMPOSE_DEV) build $(DEV_APPLICATION_SERVICE)
	$(MAKE) -f $(SCAFFOLD_ROOT)Makefile fe.setup

.PHONY: ci-ephemeral
ci-ephemeral: ## Check source and exercise the recorded release in disposable Compose
	$(MAKE) setup
	$(MAKE) check
	$(MAKE) build-release-images
	@trap 'status=$$?; $(MAKE) rm-ephemeral HOST_INGRESS=0 || { if [ "$$status" -eq 0 ]; then exit 1; fi; }; exit "$$status"' EXIT; \
		$(MAKE) verify-release-images HOST_INGRESS=0

HELP_SHARED_COMMANDS := help|help-verbose|deps\.lock|setup|ci-ephemeral|prepare-prod|migrations|migrate|superuser|shell|up-dev|down-dev|reset-dev|logs-dev|ps-dev|down-prod|logs-prod|initialize-ephemeral|up-ephemeral|down-ephemeral|rm-ephemeral|logs-ephemeral|up-prod|ps-prod|ps-ephemeral|wait-dev|wait-prod|restart-dev|restart-prod|format|format-check|smoke|smoke-prod|lint|typecheck|test|migrations-check|coverage|check|django-check|precommit|initialize-release

.PHONY: precommit
precommit: ## Run repository pre-commit hooks in the Docker toolchain
	$(COMPOSE_DEV) run --rm --no-deps --workdir /workspace \
		--user "$(HOST_UID):$(HOST_GID)" --env HOME=/tmp \
		--env SKIP="$${SKIP:+$$SKIP,}fe-eslint,fe-prettier" \
		--volume "$(GIT_COMMON_DIR):$(GIT_COMMON_DIR):ro" \
		$(DEV_APPLICATION_SERVICE) $(UV_RUN) --no-sync pre-commit run \
		--config "$(PRECOMMIT_CONFIG)" --all-files
	$(MAKE) -f $(SCAFFOLD_ROOT)Makefile fe.lint fe.fmt-check

.PHONY: initialize-release
initialize-release: ## Initialize the database and static volume with the recorded backend image
	@test -f "$(RELEASE_FILE)" || { echo 'Set RELEASE_FILE to a recorded release manifest'; exit 1; }
	$(COMPOSE_PROD) config --quiet
	$(RELEASE_PULL)
	$(COMPOSE_PROD) up -d --no-build db
	$(RUN_PROD) python -m app.manage check --deploy
	$(RUN_PROD) python -m app.manage migrate
	$(RUN_PROD) python -m app.manage collectstatic --noinput --clear

.PHONY: coverage coverage-in-container
# Source checks use Django's fallback settings without the Compose dev database.
SOURCE_CHECK_ARGS := --no-deps --env DATABASE_URL=
coverage: ## Run backend tests and enforce total and changed-line coverage in Docker
	$(call DEV_RUN_BACKEND_MAKE,$(SOURCE_CHECK_ARGS)) \
		DIFF_BASE="$(DIFF_BASE)" coverage-in-container

coverage-in-container:
	cd "$(SCAFFOLD_ROOT)backend" && $(UV_RUN) pytest -c "$(SCAFFOLD_ROOT)$(PYTHON_PROJECT_DIR)/pyproject.toml" \
		--cov=src --cov-config=pyproject.toml --cov-report=term-missing --cov-report=xml
	cd "$(SCAFFOLD_ROOT)backend" && $(UV_RUN) diff-cover coverage.xml --config-file pyproject.toml \
		$(if $(DIFF_BASE),--compare-branch=$(DIFF_BASE))

.PHONY: check django-check
check: ## Run source quality gates through the development toolchain
	$(MAKE) format-check fe.fmt-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) coverage fe.coverage
	$(MAKE) migrations-check
	$(MAKE) django-check

django-check: ## Run Django's production deployment checks
	$(COMPOSE_DEV) run --rm --no-deps \
		--env DJANGO_SETTINGS_MODULE=app.project.settings.prod \
		--env DJANGO_SECRET_KEY=check-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ \
		--env DATABASE_URL=postgresql://app:app@db:5432/app \
		$(DEV_APPLICATION_SERVICE) $(UV_RUN) python -m app.manage check --deploy

.PHONY: lint be.lint-in-container
lint: ## Run Django lint checks, then applicable frontend lint checks
	$(call DEV_RUN_BACKEND_MAKE,--no-deps) be.lint-in-container
	$(MAKE) -f $(SCAFFOLD_ROOT)Makefile fe.lint

be.lint-in-container:
	$(UV_RUN) python /workspace/backend/scripts/check_no_any.py
	$(UV_RUN) ruff check --config /workspace/backend/pyproject.toml .

.PHONY: typecheck be.typecheck-in-container
typecheck: ## Run Django type checks, then applicable frontend type checks
	$(call DEV_RUN_BACKEND_MAKE,--no-deps) be.typecheck-in-container
	$(MAKE) -f $(SCAFFOLD_ROOT)Makefile fe.typecheck

be.typecheck-in-container:
	$(UV_RUN) mypy --config-file /workspace/backend/pyproject.toml .

.PHONY: test test-in-container be.test-in-container
test: ## Run backend and applicable frontend tests in Docker
	$(call DEV_RUN_BACKEND_MAKE,--env PYTEST_ADDOPTS) be.test-in-container
	$(MAKE) -f $(SCAFFOLD_ROOT)Makefile fe.test

test-in-container: be.test-in-container fe.test-in-container

be.test-in-container:
	cd "$(SCAFFOLD_ROOT)backend" && $(UV_RUN) pytest -q -c "$(SCAFFOLD_ROOT)$(PYTHON_PROJECT_DIR)/pyproject.toml"

.PHONY: format format-check
RUFF = $(COMPOSE_DEV) run --rm --no-deps --workdir /workspace/backend $(DEV_APPLICATION_SERVICE) $(UV_RUN) ruff
format: ## Apply Python lint fixes and formatting in Docker
	$(RUFF) check --config /workspace/backend/pyproject.toml --fix .
	$(RUFF) format --config /workspace/backend/pyproject.toml .

format-check: ## Check Python formatting in Docker without source fixes
	$(RUFF) format --config /workspace/backend/pyproject.toml --check .

.PHONY: help help-verbose
help: ## List available commands
	@{ \
		grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(HELP_FILES); \
		grep -E '^($(HELP_SHARED_COMMANDS)):.*?## ' $(firstword $(MAKEFILE_LIST)); \
	} | sort -u | awk 'BEGIN {FS=":.*?## "} \
		{printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

help-verbose: help ## Alias for help

.PHONY: deps.lock
deps.lock: ## Update the selected profile's Python lockfile with pinned Dockerized uv
	docker run --rm --user "$$(id -u):$$(id -g)" \
		--env UV_CACHE_DIR=/tmp/uv-cache \
		--tmpfs /tmp:rw,mode=1777 \
		--volume "$(CURDIR):/workspace" \
		--workdir /workspace --entrypoint /usr/local/bin/uv $(UV_IMAGE) \
		lock --project $(PYTHON_PROJECT_DIR)

COMPOSE ?= $(COMPOSE_DEV)
URL_ROOT ?= $(DEV_URL_ROOT)
CURL_CMD = curl $(CURL_FLAGS) -fsS
ENV ?= dev
down-dev logs-dev ps-dev wait-dev: COMPOSE = $(COMPOSE_DEV)
down-prod logs-prod ps-prod wait-prod smoke-prod: COMPOSE = $(COMPOSE_PROD)
wait-prod smoke-prod: URL_ROOT = $(PROD_HTTPS_URL)
wait-prod smoke-prod: CURL_CMD = curl $(PROD_HTTPS_CURL_FLAGS) -fsS
down-ephemeral logs-ephemeral ps-ephemeral: COMPOSE = $(COMPOSE_EPHEMERAL)

.PHONY: down logs ps wait restart restart-dev restart-prod ps-prod ps-ephemeral wait-dev wait-prod
down down-dev down-prod down-ephemeral:
	$(COMPOSE) down --remove-orphans

logs logs-dev logs-prod logs-ephemeral:
	$(COMPOSE) logs -f --tail=200

ps ps-dev ps-prod ps-ephemeral:
	$(COMPOSE) ps

wait:
	@echo "Waiting for backend on $(URL_ROOT)/api/healthz ..."
	@for i in $$(seq 1 60); do \
		if $(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null; then \
			echo "Backend is up"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Backend did not become ready in time"; \
	$(COMPOSE) logs || true; \
	exit 1

wait-dev: wait ## Wait for development health
wait-prod: wait ## Wait for production health

.PHONY: smoke smoke-prod
smoke-prod: smoke ## Check production health, static delivery, and homepage over HTTPS
smoke: wait ## Check application health, static delivery, and homepage
	$(CURL_CMD) "$(URL_ROOT)/api/healthz" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/static/smoketest.txt" >/dev/null
	$(CURL_CMD) "$(URL_ROOT)/" | grep -qi '$(SMOKE_HOME_PATTERN)'

restart-dev: ENV := dev
restart-prod: ENV := prod
restart-dev: ## Restart development services
restart-prod: ## Restart production services
restart restart-dev restart-prod:
	$(MAKE) down-$(ENV)
	$(MAKE) up-$(ENV)

.PHONY: up-dev down-dev reset-dev logs-dev ps-dev up-prod down-prod logs-prod
up-dev: ## Build and start development services
	$(COMPOSE_DEV) up -d --build

down-dev: ## Stop development services and orphans while preserving volumes

reset-dev: ## Remove development services and volumes; requires CONFIRM_RESET=1
	@test "$(CONFIRM_RESET)" = 1 || { echo 'Refusing to remove development volumes.'; echo 'Re-run with: make reset-dev CONFIRM_RESET=1'; exit 1; }
	$(COMPOSE_DEV) down -v --remove-orphans

logs-dev: ## Follow recent development logs

ps-dev: ## List development containers
ps-prod: ## List production containers
ps-ephemeral: ## List ephemeral containers

down-prod: ## Stop production services while preserving volumes

logs-prod: ## Follow recent production logs

.PHONY: initialize-ephemeral up-ephemeral down-ephemeral rm-ephemeral logs-ephemeral
EPHEMERAL_CERTS_PATH := $(dir $(abspath $(EPHEMERAL_ENV_FILE)))certs

.PHONY: prepare-ephemeral
prepare-ephemeral: ## Prepare isolated, non-secret release smoke-test configuration
	@mkdir -p "$(dir $(EPHEMERAL_ENV_FILE))publication" "$(EPHEMERAL_CERTS_PATH)"
	@chmod 2770 "$(dir $(EPHEMERAL_ENV_FILE))publication"
	@printf '%s\n' \
		'DJANGO_SECRET_KEY=release-ci-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ' \
		'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,$(HOST_INGRESS_HOST)' \
		'DJANGO_CSRF_TRUSTED_ORIGINS=https://$(HOST_INGRESS_HOST)' \
		'POSTGRES_USER=app' \
		'POSTGRES_PASSWORD=release-ci-only' \
		'POSTGRES_DB=app' \
		'DATABASE_URL=postgresql://app:release-ci-only@db:5432/app' \
		'BACKUP_PASSWORD=ephemeral-only' \
		'BACKUP_PUBLICATION_PATH=$(dir $(EPHEMERAL_ENV_FILE))publication' \
		'BACKUP_RUNTIME_GID=$(shell id -g)' \
		'EPHEMERAL_CERTS_PATH=$(EPHEMERAL_CERTS_PATH)' \
		> "$(EPHEMERAL_ENV_FILE)"
	@if [ ! -f "$(EPHEMERAL_CERTS_PATH)/server.key" ] || \
		! openssl x509 -in "$(EPHEMERAL_CERTS_PATH)/server.crt" -noout -checkend 3600 >/dev/null 2>&1; then \
		openssl req -x509 -nodes -newkey rsa:2048 \
			-keyout "$(EPHEMERAL_CERTS_PATH)/server.key" \
			-out "$(EPHEMERAL_CERTS_PATH)/server.crt" \
			-days 1 -subj '/CN=$(HOST_INGRESS_HOST)' \
			-addext 'subjectAltName=DNS:$(HOST_INGRESS_HOST),IP:127.0.0.1,DNS:localhost'; \
	fi

initialize-ephemeral: prepare-ephemeral ## Initialize test data through the recorded-release workflow
	$(MAKE) initialize-release 'COMPOSE_PROD=$(COMPOSE_EPHEMERAL)' RELEASE_PULL=true

up-ephemeral: prepare-ephemeral ## Start the recorded release with test-environment inputs
	$(MAKE) deploy-release 'COMPOSE_PROD=$(COMPOSE_EPHEMERAL)' RELEASE_PULL=true

down-ephemeral: ## Stop ephemeral services while preserving test volumes

rm-ephemeral: ## Remove ephemeral services and disposable test volumes
	@if [ -f "$(EPHEMERAL_ENV_FILE)" ]; then $(COMPOSE_EPHEMERAL) down -v --remove-orphans; fi

logs-ephemeral: ## Follow recent ephemeral logs

LOCAL_TLS ?= 0
up-prod: prepare-prod ## Deploy the recorded production release
	$(MAKE) deploy-release

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
				-days 365 -subj '/CN=$(HOST_INGRESS_HOST)' \
				-addext 'subjectAltName=DNS:$(HOST_INGRESS_HOST),IP:127.0.0.1,DNS:localhost' || exit 1; \
		fi; \
		echo 'Local TLS certificate ready for $(HOST_INGRESS_HOST), localhost and 127.0.0.1'; \
	else \
		test -f deploy/nginx/certs/server.key && \
			openssl x509 -in deploy/nginx/certs/server.crt -noout -checkend 0 >/dev/null || \
			{ echo 'Install valid production TLS inputs, or use LOCAL_TLS=1 for the laptop dummy'; exit 1; }; \
	fi

.PHONY: migrations migrations-check migrate superuser shell
migrations: ## Create migrations for intentional model changes
	$(DJANGO_DEV_MANAGE) makemigrations

migrations-check: ## Reject model changes without migrations
	$(call DJANGO_DEV_MANAGE,$(SOURCE_CHECK_ARGS)) makemigrations --check --dry-run

migrate: ## Apply development database migrations
	$(DJANGO_DEV_MANAGE) migrate

superuser: migrate ## Migrate, then create an interactive Django superuser (development only)
	$(DJANGO_DEV_MANAGE) createsuperuser

shell: ## Open the Django shell (development only)
	$(DJANGO_DEV_MANAGE) shell

ifneq ($(SCAFFOLD_BACKUP_PROFILE),none)
include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_BACKUP_PROFILE)/profile.mk
endif

include $(SCAFFOLD_ROOT)profiles/release-transport.mk
