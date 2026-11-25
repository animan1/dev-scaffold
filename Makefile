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

include $(SCAFFOLD_ROOT)profiles/$(SCAFFOLD_PROFILE)/profile.mk


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
