SHELL := /bin/bash

PROJECT_NAME ?= $(notdir $(CURDIR))
APP_PORT ?= 18000
APP_HOST ?= localhost
DIFF_BASE ?= origin/main
PRODUCTION_IMAGE ?= local/$(PROJECT_NAME):production
GIT_COMMON_DIR ?= $(shell git rev-parse --path-format=absolute --git-common-dir)
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
COMPOSE := COMPOSE_PROJECT_NAME=$(PROJECT_NAME) APP_HOST=$(APP_HOST) APP_PORT=$(APP_PORT) \
	GIT_COMMON_DIR=$(GIT_COMMON_DIR) HOST_UID=$(HOST_UID) HOST_GID=$(HOST_GID) \
	docker compose --project-directory . -f profiles/server-rendered-django/compose.yml
RUN := $(COMPOSE) run --rm app
UV_RUN := uv run --project /workspace/profiles/server-rendered-django
PROFILE_PROJECT := /workspace/profiles/server-rendered-django/pyproject.toml

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available commands
	@grep -E '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS=":.*?## "} {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

.PHONY: build
build: ## Build the deterministic development application image
	$(COMPOSE) build app

.PHONY: up
up: ## Start Django and PostgreSQL in full-Docker development
	$(COMPOSE) up -d --build db app

.PHONY: down
down: ## Stop development services while preserving project volumes
	$(COMPOSE) down --remove-orphans

.PHONY: reset
reset: ## Destructively remove development services and volumes; requires CONFIRM_RESET=1
	@if [[ "$(CONFIRM_RESET)" != "1" ]]; then \
		echo "Refusing to remove development volumes."; \
		echo "Re-run with: make reset CONFIRM_RESET=1"; \
		exit 1; \
	fi
	$(COMPOSE) down -v --remove-orphans

.PHONY: wait
wait: ## Wait for the routed Django health endpoint
	@for attempt in $$(seq 1 60); do \
		if curl -fsS -H 'Host: $(APP_HOST)' \
			'http://127.0.0.1:$(APP_PORT)/api/healthz' >/dev/null; then \
			echo "Django is ready"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Django did not become ready"; \
	$(COMPOSE) logs app; \
	exit 1

.PHONY: format
format: ## Apply the single deterministic Python formatter and safe lint fixes
	$(RUN) $(UV_RUN) ruff check --config $(PROFILE_PROJECT) --fix .
	$(RUN) $(UV_RUN) ruff format --config $(PROFILE_PROJECT) .

.PHONY: format-check
format-check: ## Check Python formatting without mutation
	$(RUN) $(UV_RUN) ruff format --config $(PROFILE_PROJECT) --check .

.PHONY: lint
lint: ## Run strict Ruff linting
	$(RUN) $(UV_RUN) ruff check --config $(PROFILE_PROJECT) .

.PHONY: typecheck
typecheck: ## Run strict MyPy type checking
	$(RUN) $(UV_RUN) mypy --config-file $(PROFILE_PROJECT) .

.PHONY: test
test: ## Run backend tests once without the coverage gate
	$(RUN) $(UV_RUN) pytest -c $(PROFILE_PROJECT) /workspace/backend

.PHONY: coverage
coverage: ## Run the single coverage-enabled test suite and write coverage.xml
	$(RUN) $(UV_RUN) pytest -c $(PROFILE_PROJECT) --cov=src \
		--cov-config=$(PROFILE_PROJECT) --cov-report=term-missing --cov-report=xml \
		/workspace/backend

.PHONY: deps.lock
deps.lock: ## Update this profile's lockfile with pinned Dockerized uv
	docker run --rm --user "$$(id -u):$$(id -g)" \
		--env UV_CACHE_DIR=/tmp/uv-cache \
		--tmpfs /tmp:rw,mode=1777 \
		--volume "$(CURDIR):/workspace" \
		--workdir /workspace --entrypoint /usr/local/bin/uv $(UV_IMAGE) \
		lock --project profiles/server-rendered-django

.PHONY: changed-coverage
changed-coverage: ## Enforce coverage on Python lines changed from DIFF_BASE
	$(RUN) sh -lc 'cd /workspace && uv run --project profiles/server-rendered-django diff-cover \
		backend/coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=90'

.PHONY: migrations-check
migrations-check: ## Reject model changes without migrations
	$(RUN) $(UV_RUN) python -m app.manage makemigrations --check --dry-run

.PHONY: django-check
django-check: ## Run Django's production deployment checks
	$(RUN) sh -lc 'DJANGO_SETTINGS_MODULE=app.project.settings.prod \
		DJANGO_SECRET_KEY=check-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ \
		DATABASE_URL=postgresql://app:app@db:5432/app \
		$(UV_RUN) python -m app.manage check --deploy'

.PHONY: check
check: ## Run every non-image quality gate through the shared Docker toolchain
	$(MAKE) format-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) coverage
	$(MAKE) changed-coverage
	$(MAKE) migrations-check
	$(MAKE) django-check

.PHONY: build-production
build-production: ## Build the multi-stage non-root production image
	docker build -f profiles/server-rendered-django/backend.Dockerfile \
		--target production -t $(PRODUCTION_IMAGE) .
	@test "$$(docker image inspect --format='{{.Config.User}}' $(PRODUCTION_IMAGE))" = "app"

.PHONY: smoke
smoke: ## Smoke-test Django through its configurable loopback route
	$(MAKE) wait
	curl -fsS -H 'Host: $(APP_HOST)' \
		'http://127.0.0.1:$(APP_PORT)/api/healthz' | grep -q '"status": "ok"'
	curl -fsS -H 'Host: $(APP_HOST)' \
		'http://127.0.0.1:$(APP_PORT)/' | grep -q '<main'

.PHONY: verify
verify: ## Run the identical aggregate contract used by developers and CI
	$(MAKE) build
	$(MAKE) up
	$(MAKE) check
	$(MAKE) build-production
	$(MAKE) smoke

.PHONY: precommit
precommit: ## Run repository pre-commit hooks in the Docker toolchain
	$(RUN) $(UV_RUN) pre-commit run \
		--config /workspace/profiles/server-rendered-django/pre-commit-config.yaml --all-files
