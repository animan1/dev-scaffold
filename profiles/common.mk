SHELL := /bin/bash
PROJECT_NAME ?= $(notdir $(CURDIR))
RELEASE_COMPOSE_PROJECT ?= $(PROJECT_NAME)-release
RELEASE_IMAGE_PREFIX ?= local/$(PROJECT_NAME)
RELEASE_REVISION ?= $(shell git rev-parse HEAD)
RELEASE_FILE ?= deploy/releases/$(RELEASE_REVISION).env
RELEASE_PLATFORM ?=
RELEASE_HTTP_PORT ?= 18080
.DEFAULT_GOAL := help

UV_IMAGE ?= ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
SCAFFOLD_BACKUP_PROFILE ?= immutable-backup
CI_BACKUP_PROFILES ?= immutable-backup
PROD_ENV_FILE ?= deploy/.env.prod
PROD_APPLICATION_SERVICE ?= backend
PROD_CONFIG = $(COMPOSE_PROD)
HOST_INGRESS ?= 1
# Capture tenant/operator input before the smoke-test placeholder default.
PREPARE_PUBLIC_HOST := $(HOST_INGRESS_HOST)

.PHONY: initialize-release rollback-release
initialize-release: ## Initialize the database/static volume with recorded images
	@test -f $(RELEASE_FILE) || { echo 'Set RELEASE_FILE to a recorded release manifest'; exit 1; }
	$(COMPOSE_RELEASE) config --quiet
	$(RELEASE_PULL)
	$(COMPOSE_RELEASE) up -d --no-build db
	$(RUN_RELEASE) python -m app.manage check --deploy
	$(RUN_RELEASE) python -m app.manage migrate
	$(RUN_RELEASE) python -m app.manage collectstatic --noinput $(RELEASE_STATIC_ARGS)

rollback-release: deploy-release ## Deploy a previously recorded release manifest
