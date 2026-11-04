# Shared hooks; tenants may override these recipes in their own included adapter.
.PHONY: release-bundle-check release-bundle-deploy release-bundle-smoke
prepare-release-ci down-release-ci: export RELEASE_CI_COMPOSE_PROJECT := $(RELEASE_CI_COMPOSE_PROJECT)
release-bundle-check: ## Run selected-profile local quality and exact-image release gates
	@$(MAKE) prepare-release-ci
ifeq ($(SCAFFOLD_PROFILE),server-rendered-django)
	@set -e; trap '$(MAKE) down-release-ci' EXIT; \
		$(MAKE) build check; \
		$(MAKE) verify-release-images verify-host-ingress verify-monitoring-config
else
	@set -e; trap '$(MAKE) down-release-ci' EXIT; \
		$(MAKE) be.ci fe.ci; \
		$(MAKE) verify-release-images verify-host-ingress verify-monitoring-config
endif

release-bundle-deploy: deploy-release ## Reuse normal migration and deployment ordering

release-bundle-smoke: ## Verify the deployed revision, root page, and static asset
	@python3 scripts/release_bundle.py smoke

include $(dir $(lastword $(MAKEFILE_LIST)))commands.mk
