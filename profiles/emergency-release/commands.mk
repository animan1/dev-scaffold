# Operator commands shared by real tenant adapters and automated integration tests.
BUNDLE ?=
BUNDLE_SHA256 ?=
BUNDLE_INPUTS ?=
BUNDLE_RUN_ID ?=
BUNDLE_REPO ?=
BUNDLE_ARTIFACT ?=
EMERGENCY ?= 0
RELEASE_BUNDLE_TARGETS := release-bundle-capture release-bundle-hotfix release-bundle-validate release-bundle-install
$(RELEASE_BUNDLE_TARGETS): export BUNDLE := $(BUNDLE)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_SHA256 := $(BUNDLE_SHA256)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_INPUTS := $(BUNDLE_INPUTS)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_RUN_ID := $(BUNDLE_RUN_ID)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_REPO := $(BUNDLE_REPO)
$(RELEASE_BUNDLE_TARGETS): export BUNDLE_ARTIFACT := $(BUNDLE_ARTIFACT)
$(RELEASE_BUNDLE_TARGETS): export EMERGENCY := $(EMERGENCY)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_REVISION := $(RELEASE_REVISION)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_PLATFORM := $(RELEASE_PLATFORM)
$(RELEASE_BUNDLE_TARGETS): export RELEASE_FILE := $(RELEASE_FILE)
$(RELEASE_BUNDLE_TARGETS): export SCAFFOLD_PROFILE := $(SCAFFOLD_PROFILE)
release-bundle-smoke: export BUNDLE_SMOKE_URL := $(BUNDLE_SMOKE_URL)
release-bundle-smoke: export RELEASE_REVISION := $(RELEASE_REVISION)

.PHONY: release-bundle-capture release-bundle-hotfix release-bundle-validate release-bundle-install
release-bundle-capture: ## Capture one explicit successful main release and all runtime images
	python3 scripts/release_bundle.py capture

release-bundle-hotfix: ## Build/check/package an exact committed hotfix for RELEASE_PLATFORM
	python3 scripts/release_bundle.py hotfix

release-bundle-validate: ## Validate a transferred bundle and destination without deployment changes
	python3 scripts/release_bundle.py validate

release-bundle-install: ## Install offline; requires EMERGENCY=1 for this invocation
	python3 scripts/release_bundle.py install
