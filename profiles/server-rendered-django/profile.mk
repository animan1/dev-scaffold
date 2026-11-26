HELP_FILES = $(filter-out $(firstword $(MAKEFILE_LIST)),$(MAKEFILE_LIST))

PYTHON_PROJECT_DIR := profiles/server-rendered-django
APP_PORT ?= 18000
APP_HOST ?= localhost
URL_ROOT ?= http://127.0.0.1:$(APP_PORT)
CURL_FLAGS ?= -H 'Host: $(APP_HOST)'
wait-prod: URL_ROOT = http://127.0.0.1:$(RELEASE_HTTP_PORT)
wait-prod: CURL_FLAGS = -H 'Host: $(APP_HOST)' -H 'X-Forwarded-Proto: https'
DIFF_BASE ?= origin/main
PRODUCTION_IMAGE ?= local/$(PROJECT_NAME):production
GIT_COMMON_DIR ?= $(shell git rev-parse --path-format=absolute --git-common-dir)
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
COMPOSE_DEV := COMPOSE_PROJECT_NAME=$(DEV_COMPOSE_PROJECT) APP_HOST=$(APP_HOST) APP_PORT=$(APP_PORT) \
	GIT_COMMON_DIR=$(GIT_COMMON_DIR) HOST_UID=$(HOST_UID) HOST_GID=$(HOST_GID) \
	docker compose --project-directory . -f profiles/server-rendered-django/compose.yml
RUN_DEV := $(COMPOSE_DEV) run --rm app
CONTAINER_PYTHON_PROJECT_DIR := /workspace/profiles/server-rendered-django
DEV_APPLICATION_SERVICE := app
DJANGO_DEV_MANAGE := $(RUN_DEV) $(UV_RUN) python -m app.manage
PROFILE_PROJECT := $(CONTAINER_PYTHON_PROJECT_DIR)/pyproject.toml
RELEASE_BACKEND_TAG ?= $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG ?= $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
RELEASE_PULL ?= $(COMPOSE_PROD) pull app web
EPHEMERAL_HTTP_PORT ?= 18081
EPHEMERAL_HTTPS_PORT ?= 18444
EPHEMERAL_INGRESS_IMAGE ?= local/$(PROJECT_NAME)-ephemeral-ingress:test
EPHEMERAL_COMPOSE_FILE := profiles/server-rendered-django/ephemeral.compose.yml
RELEASE_COMPOSE_FILE := profiles/server-rendered-django/release.compose.yml
PROD_COMPOSE_FILES := -f $(RELEASE_COMPOSE_FILE)
COMPOSE_PROD = COMPOSE_PROJECT_NAME=$(PROD_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(PROD_ENV_FILE) docker compose --project-directory . \
	$(PROD_COMPOSE_FILES) --env-file $(PROD_ENV_FILE) --env-file $(RELEASE_FILE)
COMPOSE_EPHEMERAL = COMPOSE_PROJECT_NAME=$(EPHEMERAL_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(EPHEMERAL_ENV_FILE) EPHEMERAL_HTTP_PORT=$(EPHEMERAL_HTTP_PORT) \
	EPHEMERAL_HTTPS_PORT=$(EPHEMERAL_HTTPS_PORT) \
	EPHEMERAL_INGRESS_IMAGE=$(EPHEMERAL_INGRESS_IMAGE) \
	docker compose --project-directory . -f $(RELEASE_COMPOSE_FILE) \
	-f $(EPHEMERAL_COMPOSE_FILE) $(EPHEMERAL_BACKUP_FILES) \
	--env-file $(EPHEMERAL_ENV_FILE) --env-file $(RELEASE_FILE)
RUN_PROD = $(COMPOSE_PROD) run --rm --no-deps app
RUN_EPHEMERAL = $(COMPOSE_EPHEMERAL) run --rm --no-deps app
LOCAL_RELEASE_IMAGES = RELEASE_BACKEND_IMAGE=$(RELEASE_BACKEND_TAG) \
	RELEASE_WEB_IMAGE=$(RELEASE_WEB_TAG) RELEASE_IMAGE_PREFIX=$(RELEASE_IMAGE_PREFIX) \
	RELEASE_REVISION=$(RELEASE_REVISION) RELEASE_HTTP_PORT=$(RELEASE_HTTP_PORT)
PROD_APPLICATION_SERVICE := app
# Resolve configuration before a release manifest or its images exist.
PROD_CONFIG = $(LOCAL_RELEASE_IMAGES) COMPOSE_PROJECT_NAME=$(PROD_COMPOSE_PROJECT) \
	PROD_ENV_FILE=$(abspath $(PROD_ENV_FILE)) docker compose --project-directory . \
	$(PROD_COMPOSE_FILES) --env-file $(PROD_ENV_FILE)

.PHONY: build
build: ## Build the deterministic development application image
	$(COMPOSE_DEV) build app

.PHONY: setup
setup: build ## Prepare Docker development dependencies from the committed lockfile

.PHONY: lint
lint: ## Run strict Ruff linting
	$(RUN_DEV) $(UV_RUN) ruff check --config $(PROFILE_PROJECT) .

.PHONY: typecheck
typecheck: ## Run strict MyPy type checking
	$(RUN_DEV) $(UV_RUN) mypy --config-file $(PROFILE_PROJECT) .

.PHONY: test
test: ## Run backend tests once without the coverage gate
	$(RUN_DEV) $(UV_RUN) pytest -c $(PROFILE_PROJECT) /workspace/backend

.PHONY: coverage
coverage: ## Run the single coverage-enabled test suite and write coverage.xml
	$(RUN_DEV) $(UV_RUN) pytest -c $(PROFILE_PROJECT) --cov=src \
		--cov-config=$(PROFILE_PROJECT) --cov-report=term-missing --cov-report=xml \
		/workspace/backend

.PHONY: changed-coverage
changed-coverage: ## Enforce coverage on Python lines changed from DIFF_BASE
	$(RUN_DEV) sh -lc 'cd /workspace/backend && $(UV_RUN) diff-cover \
		coverage.xml --compare-branch=$(DIFF_BASE) --fail-under=90'

.PHONY: migrations-check
migrations-check: ## Reject model changes without migrations
	$(RUN_DEV) $(UV_RUN) python -m app.manage makemigrations --check --dry-run

.PHONY: django-check
django-check: ## Run Django's production deployment checks
	$(RUN_DEV) sh -lc 'DJANGO_SETTINGS_MODULE=app.project.settings.prod \
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
		--target production -t $(RELEASE_BACKEND_TAG) $(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) \
		--label org.opencontainers.image.revision=$(RELEASE_REVISION) .
	docker build -f profiles/server-rendered-django/release-nginx.Dockerfile \
		-t $(RELEASE_WEB_TAG) $(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) \
		--label org.opencontainers.image.revision=$(RELEASE_REVISION) .
	@test "$$(docker image inspect --format='{{.Config.User}}' $(RELEASE_BACKEND_TAG))" = "app"
	@test "$$(docker image inspect --format='{{.Config.User}}' $(RELEASE_WEB_TAG))" = "nginx"
	python3 scripts/record_local_release.py --revision $(RELEASE_REVISION) \
		--profile $(SCAFFOLD_PROFILE) --prefix $(RELEASE_IMAGE_PREFIX) \
		--backend $(RELEASE_BACKEND_TAG) --web $(RELEASE_WEB_TAG) \
		$(if $(RELEASE_PLATFORM),--platform $(RELEASE_PLATFORM)) --output $(RELEASE_FILE)

.PHONY: prepare-ephemeral
prepare-ephemeral: ## Prepare isolated, non-secret server-rendered release configuration
	@mkdir -p $(dir $(EPHEMERAL_ENV_FILE))publication
	@chmod 2770 $(dir $(EPHEMERAL_ENV_FILE))publication
	@printf '%s\n' \
		'DJANGO_SECRET_KEY=release-ci-only-abcdefghijklmnopqrstuvwxyz-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ' \
		'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1' \
		'DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost' \
		'POSTGRES_USER=app' \
		'POSTGRES_PASSWORD=release-ci-only' \
		'POSTGRES_DB=app' \
		'DATABASE_URL=postgresql://app:release-ci-only@db:5432/app' \
		'BACKUP_PASSWORD=ephemeral-only' \
		'BACKUP_PUBLICATION_PATH=$(dir $(EPHEMERAL_ENV_FILE))publication' \
		'BACKUP_RUNTIME_GID=$(shell id -g)' \
		> $(EPHEMERAL_ENV_FILE)

.PHONY: verify-release-images
verify-release-images: up-ephemeral ## Smoke-test the exact production image set
	$(RUN_EPHEMERAL) \
		sh -c "printf 'MEDIA_OK\\n' > /media/release-smoketest.txt"
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
	$(COMPOSE_EPHEMERAL) stop app
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/static/smoketest.txt' \
		| grep -q 'STATIC_OK'
	curl -fsS 'http://127.0.0.1:$(RELEASE_HTTP_PORT)/media/release-smoketest.txt' \
		| grep -q 'MEDIA_OK'

.PHONY: verify-host-ingress
verify-host-ingress: prepare-ephemeral ## Verify the loopback-only application origin boundary
	@$(COMPOSE_EPHEMERAL) config \
		| grep -q 'host_ip: 127.0.0.1'

.PHONY: verify-monitoring-config
verify-monitoring-config: prepare-ephemeral ## Validate the optional release monitor
	$(COMPOSE_EPHEMERAL) --profile monitoring config --quiet

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
	$(COMPOSE_PROD) config --quiet
	$(RELEASE_PULL)
	$(COMPOSE_PROD) up -d --no-build db
	$(RUN_PROD) python -m app.manage check --deploy
	$(RUN_PROD) python -m app.manage migrate
	$(RUN_PROD) python -m app.manage collectstatic --noinput --clear

.PHONY: deploy-release
deploy-release: initialize-release ## Deploy the digest-pinned server-rendered image set
	$(COMPOSE_PROD) up -d --no-build

prepare-ephemeral: build-ephemeral-ingress

.PHONY: build-ephemeral-ingress
build-ephemeral-ingress: ## Build the local-only TLS ingress image
	docker build -f profiles/server-rendered-django/ephemeral-ingress.Dockerfile \
		-t $(EPHEMERAL_INGRESS_IMAGE) .

.PHONY: verify-ephemeral
verify-ephemeral: ## Verify local HTTP redirect, HTTPS customer/admin routes, and secure cookies
	@for attempt in $$(seq 1 60); do \
		if curl -kfsS -H 'Host: localhost' \
			'https://127.0.0.1:$(EPHEMERAL_HTTPS_PORT)/api/healthz' >/dev/null; then \
			break; \
		fi; \
		if [[ "$$attempt" = "60" ]]; then \
			echo 'Production simulation ingress did not become ready'; exit 1; \
		fi; \
		sleep 1; \
	done
	@curl -fsSI -H 'Host: localhost' \
		'http://127.0.0.1:$(EPHEMERAL_HTTP_PORT)/' \
		| grep -Eiq '^location: https://localhost:$(EPHEMERAL_HTTPS_PORT)/'
	curl -kfsS -H 'Host: localhost' \
		'https://127.0.0.1:$(EPHEMERAL_HTTPS_PORT)/' | grep -q '<main'
	curl -kfsS -D - -o /dev/null -H 'Host: localhost' \
		'https://127.0.0.1:$(EPHEMERAL_HTTPS_PORT)/admin/' \
		| grep -Eiq '^location: /admin/login/\?next=/admin/'
	curl -kfsS -D - -o /dev/null -H 'Host: localhost' \
		'https://127.0.0.1:$(EPHEMERAL_HTTPS_PORT)/admin/login/' \
		| grep -Eiq '^set-cookie: csrftoken=.*; secure(;|[[:space:]]*$$)'

.PHONY: smoke-prod
smoke-prod: ## Check the production origin behind host-managed ingress
	curl -fsS -H 'Host: $(APP_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(RELEASE_HTTP_PORT)/api/healthz' | grep -q '\"status\": \"ok\"'
	curl -fsS -H 'Host: $(APP_HOST)' -H 'X-Forwarded-Proto: https' \
		'http://127.0.0.1:$(RELEASE_HTTP_PORT)/' | grep -q '<main'

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded release manifest
rollback-release: deploy-release

.PHONY: smoke
smoke: ## Smoke-test Django through its configurable loopback route
	$(MAKE) wait-dev
	curl -fsS -H 'Host: $(APP_HOST)' \
		'http://127.0.0.1:$(APP_PORT)/api/healthz' | grep -q '"status": "ok"'
	curl -fsS -H 'Host: $(APP_HOST)' \
		'http://127.0.0.1:$(APP_PORT)/' | grep -q '<main'

.PHONY: verify
verify: ## Run the identical aggregate contract used by developers and CI
	$(MAKE) build
	$(MAKE) up-dev
	$(MAKE) check
	$(MAKE) build-production
	$(MAKE) smoke

.PHONY: precommit
precommit: ## Run repository pre-commit hooks in the Docker toolchain
	$(RUN_DEV) $(UV_RUN) pre-commit run \
		--config /workspace/profiles/server-rendered-django/pre-commit-config.yaml --all-files
