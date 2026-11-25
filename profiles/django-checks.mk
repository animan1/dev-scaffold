# Shared Django gates reuse setup's tooling without starting application services.
SOURCE_GIT_DIR = $(shell git rev-parse --path-format=absolute --git-common-dir)
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
SOURCE_RUN = docker run --rm --pull=never --network none --read-only --tmpfs /tmp:rw,exec,mode=1777 \
	--user "$(HOST_UID):$(HOST_GID)" --volume "$(CURDIR):/workspace" \
	--volume "$(SOURCE_GIT_DIR):$(SOURCE_GIT_DIR):ro" --workdir /workspace/backend \
	--env-file "$(SCAFFOLD_ROOT)deploy/source-check.env" --env PYTEST_ADDOPTS
SOURCE_PYTHON = $(SOURCE_RUN) $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/python
SOURCE_DIFF_COVER = $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/diff-cover
SOURCE_DIFF_ARGS = --config-file $(SOURCE_CONFIG) $(if $(DIFF_BASE),--compare-branch=$(DIFF_BASE))

format-check:: ## Check formatting across the selected application's components
	$(SOURCE_PYTHON) -m ruff format --config $(SOURCE_CONFIG) --check .

lint:: ## Lint the selected application's components without fixes
	$(SOURCE_PYTHON) scripts/check_no_any.py
	$(SOURCE_PYTHON) -m ruff check --config $(SOURCE_CONFIG) .

typecheck:: ## Type-check the selected application's components
	$(SOURCE_PYTHON) -m mypy --config-file $(SOURCE_CONFIG) .

migrations-check: ## Check migration source consistency without changing schema
	$(SOURCE_PYTHON) -m app.manage makemigrations --check --dry-run

framework-check: ## Check framework configuration without a running database
	$(SOURCE_RUN) --env DJANGO_SETTINGS_MODULE=app.project.settings.check_prod $(SOURCE_IMAGE) $(SOURCE_VENV)/bin/python -m app.manage check --deploy

test:: ## Run source tests once; COVERAGE=1 also gates total and changed-line coverage
	$(SOURCE_PYTHON) -m pytest -c $(SOURCE_CONFIG) -q -p no:cacheprovider \
		$(if $(filter 1,$(COVERAGE)),--cov=src --cov-config=$(SOURCE_CONFIG) --cov-report=term-missing --cov-report=xml:coverage.xml)
ifeq ($(COVERAGE),1)
	$(SOURCE_RUN) $(SOURCE_DIFF_COVER) coverage.xml $(SOURCE_DIFF_ARGS)
endif

format:: ## Apply formatting and safe fixes across the selected application's components
	$(SOURCE_PYTHON) -m ruff check --config $(SOURCE_CONFIG) --fix .
	$(SOURCE_PYTHON) -m ruff format --config $(SOURCE_CONFIG) .
