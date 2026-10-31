# Operator commands shared by real tenant adapters and automated integration tests.
BUNDLE ?=
BUNDLE_SHA256 ?=
BUNDLE_INPUTS ?=
BUNDLE_RUN_ID ?=
BUNDLE_REPO ?=
BUNDLE_ARTIFACT ?=
BUNDLE_WORKFLOW ?= ci.yml
EMERGENCY ?= 0
BUNDLE_CAPTURE_REVISION := $(if $(filter file default undefined,$(origin RELEASE_REVISION)),,$(RELEASE_REVISION))
BUNDLE_EXPLICIT_PROJECT := $(if $(filter file default undefined,$(origin RELEASE_COMPOSE_PROJECT)),,1)
RELEASE_BUNDLE_TARGETS := release-bundle-capture release-bundle-hotfix release-bundle-validate release-bundle-install
$(RELEASE_BUNDLE_TARGETS): export BUNDLE := $(BUNDLE)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_SHA256 := $(BUNDLE_SHA256)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_INPUTS := $(BUNDLE_INPUTS)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_RUN_ID := $(BUNDLE_RUN_ID)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_REPO := $(BUNDLE_REPO)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_ARTIFACT := $(BUNDLE_ARTIFACT)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_WORKFLOW := $(BUNDLE_WORKFLOW)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_EXPLICIT_PROJECT := $(BUNDLE_EXPLICIT_PROJECT)
$(RELEASE_BUNDLE_TARGETS): export EMERGENCY := $(EMERGENCY)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_REVISION := $(RELEASE_REVISION)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_PLATFORM := $(RELEASE_PLATFORM)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_FILE := $(RELEASE_FILE)
$(RELEASE_BUNDLE_TARGETS): export SCAFFOLD_PROFILE := $(SCAFFOLD_PROFILE)
release-bundle-capture: export RELEASE_REVISION := $(BUNDLE_CAPTURE_REVISION)
# Reuse the normal selected profile's resolved deployment settings, not a second config file.
$(foreach name,PROD_ENV_FILE RELEASE_COMPOSE_PROJECT RELEASE_IMAGE_PREFIX HOST_INGRESS HOST_INGRESS_HOST HOST_INGRESS_PORT MONITORING RELEASE_HTTP_PORT RELEASE_BIND_ADDRESS RELEASE_HTTPS_PORT RELEASE_HOST PRODUCTION_COMPOSE_PROJECT PRODUCTION_HTTP_PORT PRODUCTION_SMOKE_HOST BUNDLE_SMOKE_URL,$(eval $(RELEASE_BUNDLE_TARGETS): export $(name) := $($(name))))
release-bundle-smoke: export BUNDLE_SMOKE_URL := $(BUNDLE_SMOKE_URL)
release-bundle-smoke: export RELEASE_REVISION := $(RELEASE_REVISION)

.PHONY: release-bundle-capture release-bundle-hotfix release-bundle-validate release-bundle-install
release-bundle-capture: ## Capture the latest successful main release (explicit selection optional)
	python3 scripts/release_bundle.py capture

release-bundle-hotfix: ## Build/check/package an exact committed hotfix for RELEASE_PLATFORM
	python3 scripts/release_bundle.py hotfix

release-bundle-validate: ## Validate a transferred bundle and destination without deployment changes
	python3 scripts/release_bundle.py validate

release-bundle-install: ## Install offline; requires EMERGENCY=1 for this invocation
	python3 scripts/release_bundle.py install
