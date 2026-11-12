GLOBAL_COMMAND_MAKEFILES += $(SCAFFOLD_ROOT)profiles/dependency-commands.mk
.PHONY: deps-lock
deps-lock: ## Resolve selected package locks and all declared image digest pins in Docker
	@python3 scripts/scaffold_dependencies.py "$(SCAFFOLD_PROFILE)"
