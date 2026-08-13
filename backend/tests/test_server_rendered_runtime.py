from __future__ import annotations

import os
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def test_server_rendered_compose_is_isolated_and_postgres_only() -> None:
    profile = _repository_root() / "profiles/server-rendered-django"
    compose = (profile / "compose.yml").read_text()

    assert "postgres:16.10-bookworm" in compose
    assert 'name: "${COMPOSE_PROJECT_NAME:?' in compose
    assert '"127.0.0.1:${APP_PORT:-18000}:8000"' in compose
    assert "postgres_data:" in compose
    assert "frontend:" not in compose
    assert "node:" not in compose


def test_server_rendered_production_image_is_multistage_and_non_root() -> None:
    dockerfile = (
        _repository_root() / "profiles/server-rendered-django/backend.Dockerfile"
    ).read_text()

    assert "AS development" in dockerfile
    assert "AS production" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER app" in dockerfile
