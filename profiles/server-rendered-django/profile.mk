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
RELEASE_COMPOSE_PROJECT ?= $(PROJECT_NAME)-release
RELEASE_IMAGE_PREFIX ?= local/$(PROJECT_NAME)
RELEASE_REVISION ?= $(shell git rev-parse HEAD)
RELEASE_BACKEND_TAG ?= $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG ?= $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
RELEASE_FILE ?= deploy/releases/$(RELEASE_REVISION).env
RELEASE_HTTP_PORT ?= 18080
PROD_ENV_FILE ?= deploy/.env.prod
RELEASE_CI_ENV_FILE ?= .tmp/server-rendered-release-ci.env
RELEASE_COMPOSE_FILE := profiles/server-rendered-django/release.compose.yml
COMPOSE_RELEASE = COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(PROD_ENV_FILE) docker compose --project-directory . \
	-f $(RELEASE_COMPOSE_FILE) --env-file $(PROD_ENV_FILE) --env-file $(RELEASE_FILE)
COMPOSE_RELEASE_CI = COMPOSE_PROJECT_NAME=$(RELEASE_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(RELEASE_CI_ENV_FILE) docker compose --project-directory . \
	-f $(RELEASE_COMPOSE_FILE) --env-file $(RELEASE_CI_ENV_FILE)
RUN_RELEASE = $(COMPOSE_RELEASE) run --rm --no-deps app
RUN_RELEASE_CI = $(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) run --rm --no-deps app
LOCAL_RELEASE_IMAGES = RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) \
	RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) RELEASE_IMAGE_PREFIX=$(RELEASE_IMAGE_PREFIX) \
	RELEASE_REVISION=$(RELEASE_REVISION) RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT)

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
	$(RUN) sh -lc 'cd /workspace/backend && $(UV_RUN) diff-cover \
		coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=90'

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

.PHONY: build-release-images
build-release-images: ## Build commit-addressed Django and application Nginx images once
	docker build -f profiles/server-rendered-django/backend.Dockerfile \
		--target production -t $(RELEASE_BACKEND_TAG) .
	docker build -f profiles/server-rendered-django/release-nginx.Dockerfile \
		-t $(RELEASE_WEB_TAG) .
	@test "$$(docker image inspect --format='{{.Config.User}}' $(RELEASE_BACKEND_TAG))" = "app"
	@test "$$(docker image inspect --format='{{.Config.User}}' $(RELEASE_WEB_TAG))" = "nginx"

.PHONY: prepare-release-ci
prepare-release-ci: ## Prepare isolated, non-secret server-rendered release configuration
	@mkdir -p $(dir $(RELEASE_CI_ENV_FILE))
	@printf '%s\n' \
		'DJANGO_SECRET_KEY=release-ci-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ' \
		'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1' \
		'DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost' \
		'POSTGRES_USER=app' \
		'POSTGRES_PASSWORD=release-ci-only' \
		'POSTGRES_DB=app' \
		'DATABASE_URL=postgresql://app:release-ci-only@db:5432/app' \
		> $(RELEASE_CI_ENV_FILE)

.PHONY: initialize-release-ci
initialize-release-ci: prepare-release-ci ## Initialize a fresh release test database and static volume
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) config --quiet
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) up -d --no-build db
	$(RUN_RELEASE_CI) \
		python -m app.manage check --deploy
	$(RUN_RELEASE_CI) \
		python -m app.manage migrate
	$(RUN_RELEASE_CI) \
		python -m app.manage collectstatic --noinput --clear

.PHONY: verify-release-images
verify-release-images: initialize-release-ci ## Smoke-test the exact production image set
	$(RUN_RELEASE_CI) \
		sh -c "printf 'MEDIA_OK\\n' > /media/release-smoketest.txt"
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) up -d --no-build app web
	@for attempt in $$(seq 1 60); do \
		if curl -fsS -H 'Host: localhost' -H 'X-Forwarded-Proto: https' \
			'http://127.0.0.1:$(RELEASE_HTTP_PORT)/api/healthz' >/dev/null; then \
			echo 'Server-rendered release origin is ready'; break; \
		fi; \
		if [[ "$$attempt" = "60" ]]; then \
			echo 'Server-rendered release origin did not become ready'; exit 1; \
		fi; \
		sleep 1; \
	done
	curl -fsS -H 'Host: localhost' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(RELEASE_HTTP_PORT)/' | grep -q '<main'
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/static/smoketest.txt' \
		| grep -q 'STATIC_OK'
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/media/release-smoketest.txt' \
		| grep -q 'MEDIA_OK'
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) stop app
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/static/smoketest.txt' \
		| grep -q 'STATIC_OK'
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/media/release-smoketest.txt' \
		| grep -q 'MEDIA_OK'

.PHONY: verify-host-ingress
verify-host-ingress: prepare-release-ci ## Verify the loopback-only application origin boundary
	@$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) config \
		| grep -q 'host_ip: 127.0.0.1'

.PHONY: verify-monitoring-config
verify-monitoring-config: prepare-release-ci ## Validate the optional release monitor
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) --profile monitoring config --quiet

.PHONY: down-release-ci
down-release-ci: ## Remove the isolated immutable-release verification stack
	@if [[ -f $(RELEASE_CI_ENV_FILE) ]]; then \
		$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) down -v --remove-orphans; \
	fi

.PHONY: logs-release-ci
logs-release-ci: ## Show logs from the isolated immutable-release verification stack
	$(LOCAL_RELEASE_IMAGES) $(COMPOSE_RELEASE_CI) logs --tail=200

.PHONY: push-release-images
push-release-images: ## Publish verified images and record immutable digests
	docker push $(RELEASE_BACKEND_TAG)
	docker push $(RELEASE_WEB_TAG)
	@mkdir -p $(dir $(RELEASE_FILE))
	@backend="$$(docker image inspect --format='{{index .RepoDigests 0}}' $(RELEASE_BACKEND_TAG))"; \
	web="$$(docker image inspect --format='{{index .RepoDigests 0}}' $(RELEASE_WEB_TAG))"; \
	test -n "$$backend"; test -n "$$web"; \
	printf 'SCAFFOLD_PROFILE=%s\nRELEASE_REVISION=%s\nRELEASE_IMAGE_PREFIX=%s\nRELEASE_BACKEND_IMAGE=%s\nRELEASE_WEB_IMAGE=%s\n' \
		'server-rendered-django' '$(RELEASE_REVISION)' '$(RELEASE_IMAGE_PREFIX)' \
		"$$backend" "$$web" > $(RELEASE_FILE); \
	echo "Recorded $(RELEASE_FILE)"

.PHONY: initialize-release
initialize-release: ## Initialize the database and static volume with recorded images
	@test -f $(RELEASE_FILE) || { echo 'Set RELEASE_FILE to a recorded release manifest'; exit 1; }
	$(COMPOSE_RELEASE) config --quiet
	$(COMPOSE_RELEASE) pull app web
	$(COMPOSE_RELEASE) up -d --no-build db
	$(RUN_RELEASE) python -m app.manage check --deploy
	$(RUN_RELEASE) python -m app.manage migrate
	$(RUN_RELEASE) python -m app.manage collectstatic --noinput --clear

.PHONY: deploy-release
deploy-release: initialize-release ## Deploy the digest-pinned server-rendered image set
	$(COMPOSE_RELEASE) up -d --no-build app web

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded release manifest
rollback-release: deploy-release

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
