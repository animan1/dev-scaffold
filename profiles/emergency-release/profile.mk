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

include $(dir $(lastword $(MAKEFILE_LIST)))commands.mk
