# Both release profiles expose the same transport boundary. Normal deployment
# still pulls; an offline installer must validate and load images before setting
# RELEASE_PULL=true and supplying an immutable, no-pull COMPOSE_RELEASE command.
.PHONY: release-bundle-config
RELEASE_CONFIG_FILE ?=
release-bundle-config: ## Resolve every runtime image, including optional services (stdout JSON)
ifneq ($(RELEASE_CONFIG_FILE),)
	@umask 077; mkdir -p $(dir $(RELEASE_CONFIG_FILE)); \
		$(COMPOSE_RELEASE) --profile '*' config --format json --output '$(RELEASE_CONFIG_FILE)'
	@echo 'Saved private runtime configuration to $(RELEASE_CONFIG_FILE)'
else
	@$(COMPOSE_RELEASE) --profile '*' config --format json
endif
