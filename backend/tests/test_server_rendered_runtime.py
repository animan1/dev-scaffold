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


def test_server_rendered_production_paths_include_persistent_media() -> None:
    repository = _repository_root()
    base_settings = (repository / "backend/src/app/project/settings/base.py").read_text()
    production_settings = (repository / "backend/src/app/project/settings/prod.py").read_text()
    dockerfile = (repository / "profiles/server-rendered-django/backend.Dockerfile").read_text()

    assert 'MEDIA_URL = "/media/"' in base_settings
    assert 'MEDIA_ROOT = "/media/"' in production_settings
    assert "mkdir -p /media /static" in dockerfile
    assert "/home/app /media /static" in dockerfile


def test_server_rendered_web_owns_static_and_media_delivery_only() -> None:
    profile = _repository_root() / "profiles/server-rendered-django"
    compose = (profile / "release.compose.yml").read_text()
    nginx = (profile / "release-nginx.conf").read_text()
    dockerfile = (profile / "release-nginx.Dockerfile").read_text()

    assert '"127.0.0.1:${RELEASE_HTTP_PORT:-18080}:8080"' in compose
    assert "- media:/media" in compose
    assert "- media:/media:ro" in compose
    assert "- staticfiles:/static:ro" in compose
    assert "/var/run/docker.sock" not in compose
    web = compose.split("  web:", 1)[1].split("\n  monitor:", 1)[0]
    assert "DJANGO_SECRET_KEY" not in web
    assert "location /static/" in nginx
    assert "location /media/" in nginx
    assert "proxy_pass http://app:8000" in nginx
    assert "ssl_certificate" not in nginx
    assert "listen 443" not in nginx
    assert "USER nginx" in dockerfile
    assert (
        "FROM nginx:1.27.5-alpine@sha256:"
        "65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
    ) in dockerfile
