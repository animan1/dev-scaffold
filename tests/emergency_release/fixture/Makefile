RELEASE_IMAGE_PREFIX ?= local/exercise
RELEASE_BACKEND_TAG = $(RELEASE_IMAGE_PREFIX)-backend:$(RELEASE_REVISION)
RELEASE_WEB_TAG = $(RELEASE_IMAGE_PREFIX)-web:$(RELEASE_REVISION)
COMPOSE_RELEASE = PROD_ENV_FILE=$(PROD_ENV_FILE) docker compose -p $(RELEASE_COMPOSE_PROJECT) -f compose.yml --env-file $(PROD_ENV_FILE) --env-file $(RELEASE_FILE)
RELEASE_PULL ?= $(COMPOSE_RELEASE) pull
export RELEASE_REVISION BUNDLE_SMOKE_URL
include profiles/emergency-release/commands.mk

build-release-images:
	docker build --network none --platform $(RELEASE_PLATFORM) --label org.opencontainers.image.revision=$(RELEASE_REVISION) -t $(RELEASE_BACKEND_TAG) -t $(RELEASE_WEB_TAG) .

release-bundle-check:
	docker run --rm --network none $(RELEASE_BACKEND_TAG) python -m unittest app_test

release-bundle-config:
	@$(COMPOSE_RELEASE) --profile '*' config --format json

release-bundle-deploy:
	$(RELEASE_PULL)
	$(COMPOSE_RELEASE) run --rm --no-deps app python app.py migrate
	$(COMPOSE_RELEASE) up -d --no-build

release-bundle-smoke:
	$(COMPOSE_RELEASE) exec -T app python app.py smoke
