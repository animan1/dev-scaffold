REMOTE_DB_PROFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
PROJECT_NAME ?= $(notdir $(CURDIR))
REMOTE_DB_TUNNEL_IMAGE ?= local/$(PROJECT_NAME)-remote-production-db-tunnel:dev
REMOTE_DB_TUNNEL_SERVICE ?= production-db-tunnel
REMOTE_DB_STAGING_COMPOSE_FILE ?= $(REMOTE_DB_PROFILE_DIR)staging.compose.yml
REMOTE_DB_PRODUCTION_COMPOSE_FILE ?= $(REMOTE_DB_PROFILE_DIR)production.compose.yml
REMOTE_DB_TUNNEL_COMPOSE ?= $(COMPOSE_STAGING)
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
