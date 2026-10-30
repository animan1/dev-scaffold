# Both release profiles expose the same transport boundary. Normal deployment
# still pulls; an offline installer must validate and load images before setting
# RELEASE_PULL=true and supplying an immutable, no-pull COMPOSE_RELEASE command.
.PHONY: release-bundle-config release-transport-test
release-bundle-config: ## Resolve every runtime image, including optional services (stdout JSON)
	@$(COMPOSE_RELEASE) --profile '*' config --format json

release-transport-test: ## Verify release transport hooks without Docker or network access
	backend/.venv/bin/pytest -q backend/tests/test_release_transport.py backend/tests/test_immutable_release_profiles.py
