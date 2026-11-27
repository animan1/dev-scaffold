DEV_COMPOSE_FILE := deploy/docker-compose.dev.yml
COMPOSE_PROJECT_DIR := deploy
APPLICATION_SERVICE := backend
CONTAINER_PYTHON_PROJECT_DIR := /app
FRONTEND_DIR := frontend
RELEASE_BACKEND_CONTEXT := backend
RELEASE_BACKEND_DOCKERFILE := backend/Dockerfile
RELEASE_WEB_DOCKERFILE := deploy/nginx/Dockerfile
MONITORING ?= 0
MONITORING_COMPOSE_FILE := deploy/docker-compose.external-monitoring.yml
EPHEMERAL_HTTP_PORT ?= $(RELEASE_HTTP_PORT)
EPHEMERAL_HTTPS_PORT ?= 18443
RELEASE_COMPOSE_FILES := -f deploy/docker-compose.prod.yml -f deploy/docker-compose.release.yml
PROD_COMPOSE_FILES := $(RELEASE_COMPOSE_FILES)
EPHEMERAL_COMPOSE_FILES = $(RELEASE_COMPOSE_FILES) \
	-f $(if $(filter 1,$(HOST_INGRESS)),deploy/docker-compose.host-ingress.yml,deploy/docker-compose.ephemeral.yml)
PROD_COMPOSE_PROFILES :=
ifeq ($(HOST_INGRESS),1)
PROD_COMPOSE_FILES += -f deploy/docker-compose.host-ingress.yml
endif
ifeq ($(MONITORING),1)
PROD_COMPOSE_FILES += -f $(MONITORING_COMPOSE_FILE)
PROD_COMPOSE_PROFILES += --profile monitoring
endif
PROD_WEB_SERVICE := nginx

# ---- Paths ----
PYTHON_PROJECT_DIR := backend
SMOKE_HOME_PATTERN := dev-scaffold\|id="root"

# ---- Frontend (Vite/React/TS) ----
# List backend prerequisites first to preserve serial execution order.
.PHONY: fe.setup fe.setup-in-container
setup: be.setup fe.setup
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
precommit: be.precommit fe.lint fe.fmt-check
lint: be.lint fe.lint
fe.lint: ## ESLint (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm lint --max-warnings 0

.PHONY: fe.format
fe.format: ## Prettier write (frontend)
	cd $(FRONTEND_DIR) && pnpm format

.PHONY: fe.fmt-check
format-check: be.format-check fe.fmt-check
fe.fmt-check: ## Prettier check (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm fmt-check

.PHONY: fe.test fe.test-in-container
test: be.test fe.test
test-in-container: be.test-in-container fe.test-in-container
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
typecheck: be.typecheck fe.typecheck
fe.typecheck: ## TypeScript typecheck (frontend)
	$(COMPOSE_DEV) run --rm --no-deps \
		--volume "$(abspath $(FRONTEND_DIR))/node_modules:/app/node_modules" \
		frontend corepack pnpm typecheck

.PHONY: fe.coverage
coverage: be.coverage fe.coverage
fe.coverage:
	$(COMPOSE_DEV) run --rm --no-deps --workdir /workspace/frontend \
		frontend make --no-print-directory -f /workspace/Makefile SCAFFOLD_PROFILE=react-vite \
		FRONTEND_DIR=/workspace/frontend COVERAGE=1 fe.setup-in-container fe.test-in-container
	$(COMPOSE_DEV) run --rm --no-deps --workdir /workspace/frontend \
		--volume "$(GIT_COMMON_DIR):$(GIT_COMMON_DIR):ro" \
		$(APPLICATION_SERVICE) $(CONTAINER_PYTHON_PROJECT_DIR)/.venv/bin/diff-cover coverage/lcov.info \
		--config-file /workspace/backend/pyproject.toml $(if $(DIFF_BASE),--compare-branch=$(DIFF_BASE))
