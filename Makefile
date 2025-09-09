SHELL := /bin/bash
COMPOSE_DEV := docker compose -f deploy/docker-compose.dev.yml

# ---- Paths ----
PY_DIR := backend

# ---- Help ----
help:
	@echo "Targets:"
	@echo "  setup           Install Python deps (uv) and pre-commit hooks"
	@echo "  hooks           Install pre-commit hooks"
	@echo "  verify          Run lint, typecheck, tests, coverage (all)"
	@echo "  preflight       Pre-commit all-in-one (format, lint, typecheck, tests, coverage, hooks)"
	@echo "  backend.run     Run Django dev server (app.settings.dev)"
	@echo "  migrate         Run Django migrations"
	@echo "  superuser       Create Django superuser"
	@echo "  lint            Ruff lint"
	@echo "  format          Apply Ruff format + Black"
	@echo "  fmt-check       Check formatting without changing files"
	@echo "  typecheck       Mypy type checking"
	@echo "  test            Pytest"
	@echo "  coverage        Pytest with coverage and gate"
	@echo "  precommit       Run pre-commit on all files"
	@echo "  clean           Remove caches and build artifacts"

# ---- Docker / Compose ----
.PHONY: up
up:
	$(COMPOSE_DEV) up --build -d

.PHONY: down
down:
	$(COMPOSE_DEV) down -v

.PHONY: logs
logs:
	$(COMPOSE_DEV) logs -f --tail=200

.PHONY: ps
ps:
	$(COMPOSE_DEV) ps

.PHONY: bash-backend
bash-backend:
	$(COMPOSE_DEV) exec backend bash

# CI helper: run backend verify inside container (once frontend is added, we can add an FE target too)
.PHONY: ci-backend
ci-backend:
	$(COMPOSE_DEV) run --rm backend bash -lc "cd /app && uv run ruff check . && uv run mypy . && uv run pytest --cov=src --cov-report=term-missing"

# ---- Django dev helpers ----
.PHONY: backend.run
backend.run:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage runserver 0.0.0.0:8000

.PHONY: migrate
migrate:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage migrate

.PHONY: superuser
superuser:
	cd $(PY_DIR)/src && DJANGO_SETTINGS_MODULE=app.project.settings.dev uv run python -m app.manage createsuperuser

# ---- Setup / Hooks ----
.PHONY: setup
setup:
	cd $(PY_DIR) && uv sync --all-extras
	uv run pre-commit install

.PHONY: hooks
hooks:
	uv run pre-commit install

# ---- Quality gates ----
.PHONY: verify
verify: lint typecheck test coverage

.PHONY: preflight
preflight: format lint typecheck coverage precommit
	@echo "✅ Preflight complete."

.PHONY: lint
lint:
	cd $(PY_DIR) && uv run ruff check .

.PHONY: format
format:
	cd $(PY_DIR) && uv run ruff check --fix .
	cd $(PY_DIR) && uv run ruff format .
	cd $(PY_DIR) && uv run black .

.PHONY: fmt-check
fmt-check:
	cd $(PY_DIR) && uv run ruff format --check .
	cd $(PY_DIR) && uv run black --check .

.PHONY: typecheck
typecheck:
	cd $(PY_DIR) && uv run mypy .

.PHONY: test
test:
	cd $(PY_DIR) && uv run pytest -q

.PHONY: coverage
coverage:
	cd $(PY_DIR) && uv run pytest --cov=src --cov-report=term-missing

# ---- Pre-commit runner ----
.PHONY: precommit
precommit:
	uv run pre-commit run --all-files

# ---- Clean ----
.PHONY: clean
clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf $(PY_DIR)/.pytest_cache $(PY_DIR)/.mypy_cache
