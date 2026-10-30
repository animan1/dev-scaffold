REMOTE_DB_PROFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME ?= $(notdir $(CURDIR))
REMOTE_DB_TUNNEL_IMAGE ?= local/$(PROJECT_NAME)-remote-production-db-tunnel:dev
REMOTE_DB_TUNNEL_SERVICE ?= production-db-tunnel
REMOTE_DB_STAGING_COMPOSE_FILE ?= $(REMOTE_DB_PROFILE_DIR)staging.compose.yml
REMOTE_DB_PRODUCTION_COMPOSE_FILE ?= $(REMOTE_DB_PROFILE_DIR)production.compose.yml
REMOTE_DB_TUNNEL_COMPOSE ?= $(COMPOSE_STAGING)
REMOTE_DB_EXERCISE_ROOT ?= $(CURDIR)/.tmp/remote-production-db-exercise
REMOTE_DB_EXERCISE_SSH_PORT ?= 22222
REMOTE_DB_EXERCISE_REMOTE_PROJECT ?= $(PROJECT_NAME)-remote-db-exercise
REMOTE_DB_EXERCISE_STAGING_PROJECT ?= $(PROJECT_NAME)-staging-db-exercise
REMOTE_DB_EXERCISE_REMOTE_COMPOSE = COMPOSE_PROJECT_NAME=$(REMOTE_DB_EXERCISE_REMOTE_PROJECT) \
	REMOTE_DB_EXERCISE_ROOT=$(REMOTE_DB_EXERCISE_ROOT) \
	REMOTE_DB_EXERCISE_SSH_PORT=$(REMOTE_DB_EXERCISE_SSH_PORT) \
	docker compose --project-directory . \
	-f $(REMOTE_DB_PRODUCTION_COMPOSE_FILE) \
	-f $(REMOTE_DB_PROFILE_DIR)exercise/remote.compose.yml
REMOTE_DB_EXERCISE_STAGING_COMPOSE = \
	COMPOSE_PROJECT_NAME=$(REMOTE_DB_EXERCISE_STAGING_PROJECT) \
	REMOTE_DB_TUNNEL_IMAGE=$(REMOTE_DB_TUNNEL_IMAGE) \
	STAGING_DB_SSH_HOST=host.docker.internal \
	STAGING_DB_SSH_PORT=$(REMOTE_DB_EXERCISE_SSH_PORT) \
	STAGING_DB_SSH_USER=tunnel \
	STAGING_DB_SSH_IDENTITY_FILE=$(REMOTE_DB_EXERCISE_ROOT)/client \
	STAGING_DB_SSH_KNOWN_HOSTS_FILE=$(REMOTE_DB_EXERCISE_ROOT)/known_hosts \
	REMOTE_DB_HOST=host.docker.internal REMOTE_DB_PORT=15432 \
	docker compose --project-directory . \
	-f $(REMOTE_DB_STAGING_COMPOSE_FILE) \
	-f $(REMOTE_DB_PROFILE_DIR)exercise/staging.compose.yml
export REMOTE_DB_TUNNEL_IMAGE

.PHONY: build-staging-db-tunnel
build-staging-db-tunnel: ## Build the laptop-side SSH database tunnel image
	docker build -f $(REMOTE_DB_PROFILE_DIR)Dockerfile \
		-t $(REMOTE_DB_TUNNEL_IMAGE) .

.PHONY: up-staging-db-tunnel
up-staging-db-tunnel: build-staging-db-tunnel ## Start and health-check the staging database tunnel
	$(REMOTE_DB_TUNNEL_COMPOSE) up -d --wait --no-build $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: status-staging-db-tunnel
status-staging-db-tunnel: ## Show the staging database tunnel status
	$(REMOTE_DB_TUNNEL_COMPOSE) ps $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: logs-staging-db-tunnel
logs-staging-db-tunnel: ## Show recent staging database tunnel logs
	$(REMOTE_DB_TUNNEL_COMPOSE) logs --tail=200 $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: down-staging-db-tunnel
down-staging-db-tunnel: ## Stop only the staging database tunnel
	$(REMOTE_DB_TUNNEL_COMPOSE) stop $(REMOTE_DB_TUNNEL_SERVICE)
	$(REMOTE_DB_TUNNEL_COMPOSE) rm -f $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: verify-staging-db-tunnel-compose
verify-staging-db-tunnel-compose: ## Validate the selected staging tunnel Compose configuration
	$(REMOTE_DB_TUNNEL_COMPOSE) config --quiet

.PHONY: prepare-remote-db-tunnel-exercise
prepare-remote-db-tunnel-exercise: build-staging-db-tunnel
	@mkdir -p $(REMOTE_DB_EXERCISE_ROOT)
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) down -v --remove-orphans
	$(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) down -v --remove-orphans
	docker run --rm \
		-v $(REMOTE_DB_EXERCISE_ROOT):/work \
		-v $(REMOTE_DB_PROFILE_DIR)exercise/prepare.sh:/prepare.sh:ro \
		--entrypoint sh $(REMOTE_DB_TUNNEL_IMAGE) /prepare.sh

.PHONY: up-remote-db-tunnel-exercise
up-remote-db-tunnel-exercise: prepare-remote-db-tunnel-exercise ## Start isolated remote-production and laptop-staging environments
	$(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) up -d --build --wait
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) up -d --wait --no-build $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: exercise-remote-db-tunnel
exercise-remote-db-tunnel: up-remote-db-tunnel-exercise ## Prove SSH-only shared-data access and production independence
	@test "$$($(REMOTE_DB_EXERCISE_STAGING_COMPOSE) run --rm staging-probe \
		--tuples-only --no-align \
		--command 'SELECT value FROM scaffold_tunnel_probe WHERE id = 1')" \
		= "remote-production-data"
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) stop $(REMOTE_DB_TUNNEL_SERVICE)
	@if $(REMOTE_DB_EXERCISE_STAGING_COMPOSE) run --rm --no-deps staging-probe \
		--tuples-only --no-align --command 'SELECT 1'; then \
		echo "Staging unexpectedly reached PostgreSQL without the SSH tunnel."; \
		exit 1; \
	fi
	@test "$$($(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) exec -T db psql \
		--username scaffold_remote --dbname scaffold_remote \
		--tuples-only --no-align \
		--command 'SELECT value FROM scaffold_tunnel_probe WHERE id = 1')" \
		= "remote-production-data"
	@echo "Remote production data remained available after the laptop tunnel stopped."

.PHONY: status-remote-db-tunnel-exercise
status-remote-db-tunnel-exercise: ## Show both isolated exercise environments
	$(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) ps
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) ps

.PHONY: logs-remote-db-tunnel-exercise
logs-remote-db-tunnel-exercise: ## Show remote database, SSH, and laptop tunnel exercise logs
	$(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) logs --tail=200
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) logs --tail=200 $(REMOTE_DB_TUNNEL_SERVICE)

.PHONY: down-remote-db-tunnel-exercise
down-remote-db-tunnel-exercise: ## Remove only the disposable tunnel exercise environments and volumes
	$(REMOTE_DB_EXERCISE_STAGING_COMPOSE) down -v --remove-orphans
	$(REMOTE_DB_EXERCISE_REMOTE_COMPOSE) down -v --remove-orphans
