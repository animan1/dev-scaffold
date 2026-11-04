GLOBAL_COMMAND_MAKEFILES += $(SCAFFOLD_ROOT)profiles/source-commands.mk
export PROJECT_NAME SCAFFOLD_PROFILE SCAFFOLD_BACKUP_PROFILE CI_PROFILES CI_BACKUP_PROFILES
export COVERAGE DIFF_BASE RELEASE_PLATFORM

.PHONY: setup hooks check format-check lint typecheck migrations-check framework-check test format
setup: ## Prepare the selected profile's Docker source tooling
hooks: ## Install this checkout's Docker source-check hook
check: ## Run all source gates and changed-line coverage without starting the application
format-check: ## Check backend and selected frontend formatting
lint: ## Lint the selected application
typecheck: ## Type-check the selected application
migrations-check: ## Reject missing Django migrations
framework-check: ## Check Django production settings without starting services
test: ## Test the selected application; COVERAGE=1 gates changed lines
format: ## Apply formatting and safe lint fixes
setup hooks check format-check lint typecheck migrations-check framework-check test format:
	@python3 scripts/scaffold_source.py "$@"
