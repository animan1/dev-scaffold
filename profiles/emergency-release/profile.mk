# Shared hooks; tenants may override these recipes in their own included adapter.
.PHONY: release-bundle-check release-bundle-deploy release-bundle-smoke
release-bundle-check: ## Run selected-profile local quality and exact-image release gates
ifeq ($(SCAFFOLD_PROFILE),server-rendered-django)
	@set -e; trap '$(MAKE) down-release-ci down' EXIT; \
		$(MAKE) build up check; \
		$(MAKE) verify-release-images verify-host-ingress verify-monitoring-config
else
	@set -e; trap '$(MAKE) down-release-ci down' EXIT; \
		$(MAKE) up be.wait be.ci fe.ci smoke; \
		$(MAKE) verify-release-images verify-host-ingress verify-monitoring-config
endif

release-bundle-deploy: deploy-release ## Reuse normal migration and deployment ordering

release-bundle-smoke: ## Verify the deployed revision, root page, and static asset
	@python3 scripts/release_bundle.py smoke

BUNDLE ?=
BUNDLE_SHA256 ?=
BUNDLE_INPUTS ?=
BUNDLE_RUN_ID ?=
BUNDLE_REPO ?=
BUNDLE_ARTIFACT ?=
EMERGENCY ?= 0
export BUNDLE BUNDLE_SHA256 BUNDLE_INPUTS BUNDLE_RUN_ID BUNDLE_REPO BUNDLE_ARTIFACT EMERGENCY
export BUNDLE_SMOKE_URL
export RELEASE_REVISION RELEASE_PLATFORM RELEASE_FILE SCAFFOLD_PROFILE

.PHONY: release-bundle-capture release-bundle-hotfix release-bundle-validate release-bundle-install release-bundle-test release-bundle-exercise release-contract-test
release-bundle-capture: ## Capture one explicit successful main release and all runtime images
	python3 scripts/release_bundle.py capture

release-bundle-hotfix: ## Build/check/package an exact committed hotfix for RELEASE_PLATFORM
	python3 scripts/release_bundle.py hotfix

release-bundle-validate: ## Validate a transferred bundle and destination without deployment changes
	python3 scripts/release_bundle.py validate

release-bundle-install: ## Install offline; requires EMERGENCY=1 for this invocation
	python3 scripts/release_bundle.py install

release-bundle-test: ## Run offline bundle boundary and existing release contract tests
	python3 -m unittest discover -s tests/emergency_release -p 'test_*.py' -v

release-contract-test: ## Verify the existing profile/Make release contracts
	backend/.venv/bin/pytest -q backend/tests/test_immutable_release_profiles.py backend/tests/test_makefile_safety.py backend/tests/test_release_transport.py

release-bundle-exercise: ## Exercise both paths with isolated offline Docker engines
	python3 tests/emergency_release/exercise.py
