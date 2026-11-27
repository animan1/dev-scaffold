HELP_FILES = $(filter-out $(firstword $(MAKEFILE_LIST)),$(MAKEFILE_LIST))

PYTHON_PROJECT_DIR := profiles/server-rendered-django
SMOKE_HOME_PATTERN := <main
DEV_COMPOSE_FILE := profiles/server-rendered-django/compose.yml
COMPOSE_PROJECT_DIR := .
CONTAINER_PYTHON_PROJECT_DIR := /workspace/profiles/server-rendered-django
APPLICATION_SERVICE := app
PROD_WEB_SERVICE := web
EPHEMERAL_HTTP_PORT ?= 18081
EPHEMERAL_HTTPS_PORT ?= 18444
RELEASE_COMPOSE_FILE := profiles/server-rendered-django/release.compose.yml
PROD_COMPOSE_FILES = -f $(RELEASE_COMPOSE_FILE) \
	$(if $(filter 0,$(HOST_INGRESS)),-f profiles/server-rendered-django/standalone.compose.yml)
EPHEMERAL_COMPOSE_FILES = $(PROD_COMPOSE_FILES)

RELEASE_BACKEND_CONTEXT := .
RELEASE_BACKEND_DOCKERFILE := profiles/server-rendered-django/backend.Dockerfile
RELEASE_WEB_DOCKERFILE := profiles/server-rendered-django/release-nginx.Dockerfile
RELEASE_BACKEND_USER := app
RELEASE_WEB_USER := nginx

EPHEMERAL_MEDIA_SMOKE := /media/release-smoketest.txt
integration-test-ephemeral: seed-ephemeral-media
.PHONY: seed-ephemeral-media
seed-ephemeral-media:
	$(COMPOSE_EPHEMERAL) exec -T $(APPLICATION_SERVICE) \
		sh -c "printf 'MEDIA_OK\\n' > $(EPHEMERAL_MEDIA_SMOKE)"
