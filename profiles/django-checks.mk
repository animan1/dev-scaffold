# Reuse the selected profile's disposable development/test container.
format-check:: ## Check formatting across the selected application's components
	$(SOURCE_RUN) $(SOURCE_UV) ruff format --config $(SOURCE_CONFIG) --check .

lint:: ## Lint the selected application's components without fixes
	$(SOURCE_RUN) $(SOURCE_UV) ruff check --config $(SOURCE_CONFIG) .

typecheck:: ## Type-check the selected application's components
	$(SOURCE_RUN) $(SOURCE_UV) mypy --config-file $(SOURCE_CONFIG) .

migrations-check: ## Check migration source consistency without changing schema
	$(SOURCE_RUN) $(SOURCE_UV) python -m app.manage makemigrations --check --dry-run

framework-check: ## Run Django's production deployment checks
	$(SOURCE_RUN) env DJANGO_SETTINGS_MODULE=app.project.settings.prod $(SOURCE_UV) python -m app.manage check --deploy

test:: ## Run source tests once; COVERAGE=1 enables the existing coverage gates
	$(SOURCE_RUN) $(SOURCE_UV) pytest -c $(SOURCE_CONFIG) -q \
		$(if $(filter 1,$(COVERAGE)),--cov=src --cov-config=$(SOURCE_CONFIG) --cov-report=term-missing --cov-report=xml)

format:: ## Apply formatting and safe fixes across the selected application's components
	$(SOURCE_RUN) $(SOURCE_UV) ruff check --config $(SOURCE_CONFIG) --fix .
	$(SOURCE_RUN) $(SOURCE_UV) ruff format --config $(SOURCE_CONFIG) .
