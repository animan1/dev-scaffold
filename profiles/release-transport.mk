# Both release profiles expose the same transport boundary. Normal deployment
# still pulls; an offline installer must validate and load images before setting
# RELEASE_PULL=true and supplying an immutable, no-pull COMPOSE_RELEASE command.
.PHONY: release-bundle-config
release-bundle-config: ## Resolve every runtime image, including optional services (stdout JSON)
	@$(COMPOSE_RELEASE) --profile '*' config --format json
