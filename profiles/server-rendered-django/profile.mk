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
RELEASE_BACKEND_STAGE := production
RELEASE_WEB_DOCKERFILE := profiles/server-rendered-django/release-nginx.Dockerfile
RELEASE_BACKEND_USER := app
RELEASE_WEB_USER := nginx

EPHEMERAL_MEDIA_SMOKE := /media/release-smoketest.txt
integration-test-ephemeral: seed-ephemeral-media
.PHONY: seed-ephemeral-media
seed-ephemeral-media:
	$(COMPOSE_EPHEMERAL) exec -T $(APPLICATION_SERVICE) \
		sh -c "printf 'MEDIA_OK\\n' > $(EPHEMERAL_MEDIA_SMOKE)"

.PHONY: verify-host-ingress
verify-host-ingress: HOST_INGRESS := 1
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

.PHONY: deploy-release
deploy-release: initialize-release ## Deploy the digest-pinned server-rendered image set
	$(COMPOSE_PROD) up -d --no-build

.PHONY: verify-ephemeral
verify-ephemeral: ## Verify local HTTP redirect, HTTPS customer/admin routes, and secure cookies
	@for attempt in $$(seq 1 60); do \
		if $(EPHEMERAL_CURL) -H 'Host: $(HOST_INGRESS_HOST)' \
			'$(EPHEMERAL_HTTPS_URL)/api/healthz' >/dev/null; then \
			break; \
		fi; \
		if [[ "$$attempt" = "60" ]]; then \
			echo 'Production simulation ingress did not become ready'; exit 1; \
		fi; \
		sleep 1; \
	done
	@curl -fsSI -H 'Host: $(HOST_INGRESS_HOST)' \
		'http://127.0.0.1:$(EPHEMERAL_HTTP_PORT)/' \
		| grep -Eiq '^location: https://$(HOST_INGRESS_HOST):$(EPHEMERAL_HTTPS_PORT)/'
	$(EPHEMERAL_CURL) -H 'Host: $(HOST_INGRESS_HOST)' \
		'$(EPHEMERAL_HTTPS_URL)/' | grep -q '<main'
	$(EPHEMERAL_CURL) '$(EPHEMERAL_HTTPS_URL)/static/smoketest.txt' | grep -q 'STATIC_OK'
	$(EPHEMERAL_CURL) -D - -o /dev/null -H 'Host: $(HOST_INGRESS_HOST)' \
		'$(EPHEMERAL_HTTPS_URL)/admin/' \
		| grep -Eiq '^location: /admin/login/\?next=/admin/'
	$(EPHEMERAL_CURL) -D - -o /dev/null -H 'Host: $(HOST_INGRESS_HOST)' \
		'$(EPHEMERAL_HTTPS_URL)/admin/login/' \
		| grep -Eiq '^set-cookie: csrftoken=.*; secure(;|[[:space:]]*$$)'

.PHONY: rollback-release
rollback-release: ## Deploy a previously recorded release manifest
rollback-release: deploy-release
