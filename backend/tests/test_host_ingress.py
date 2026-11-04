from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

import pytest


def _repo_root() -> Path:
    candidates = (
        Path(os.environ.get("REPO_DIR", "")),
        Path(__file__).resolve().parents[2],
        Path("/workspace"),
    )
    for candidate in candidates:
        if (candidate / "Makefile").is_file():
            return candidate
    raise FileNotFoundError("Could not find the repository root")


def test_host_ingress_origin_is_loopback_http_only() -> None:
    compose = (_repo_root() / "deploy/docker-compose.host-ingress.yml").read_text()
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert '"127.0.0.1:${HOST_INGRESS_PORT:-18080}:80"' in compose
    assert "/etc/nginx/certs" not in compose
    assert "listen 80;" in nginx
    assert "listen 443" not in nginx
    assert "ssl_certificate" not in nginx


def test_host_ingress_templates_only_the_selected_backend() -> None:
    compose = (_repo_root() / "deploy/docker-compose.host-ingress.yml").read_text()
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "${BACKEND_HOST:-backend}" in compose
    assert 'NGINX_ENVSUBST_FILTER: "^BACKEND_HOST$"' in compose
    assert "/etc/nginx/templates/default.conf.template:ro" in compose
    assert 'set $application_upstream "${BACKEND_HOST}:8000";' in nginx
    assert nginx.count("proxy_pass http://$application_upstream;") == 2
    assert "$host" in nginx
    assert "$trusted_forwarded_proto" in nginx


def test_host_ingress_routes_django_admin_without_capturing_frontend_routes() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "location = /admin {" in nginx
    assert "absolute_redirect off;" in nginx
    assert "return 301 /admin/;" in nginx
    assert "location /admin/ {" in nginx
    assert "location /api/ {" in nginx
    assert "location /static/ {" in nginx
    assert "location /assets/ {" in nginx
    assert "location / {" in nginx


def test_production_proxies_refresh_dns_without_recreating_other_services() -> None:
    for filename in (
        "deploy/nginx/default.host-ingress.conf",
        "profiles/server-rendered-django/release-nginx.conf",
    ):
        nginx = (_repo_root() / filename).read_text()
        assert "resolver 127.0.0.11 ipv6=off valid=5s;" in nginx
        assert "proxy_pass http://$application_upstream;" in nginx


def test_host_ingress_normalizes_forwarding_headers_at_the_proxy_boundary() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "map $http_x_forwarded_proto $trusted_forwarded_proto" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto;" in nginx


def test_preparation_preserves_existing_credentials_without_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator_module: ModuleType
) -> None:
    root = tmp_path / "checkout"
    monkeypatch.setenv("DEPLOYMENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PROD_ENV_FILE", "")
    op = operator_module.Operator(root)
    path = op.inputs("prod")
    text = "DJANGO_SECRET_KEY=preserve-me\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=existing-password\nPOSTGRES_DB=app\nBACKUP_PASSWORD=existing-backup-key\n"
    operator_module.atomic(path, text)
    monkeypatch.setattr(
        operator_module, "run", lambda *a, **k: pytest.fail("preparation must not start services")
    )
    op.prepare("prod")
    first = path.read_text()
    op.prepare("prod")
    assert path.read_text() == first
    assert first.startswith(text)
    assert path.stat().st_mode & 0o077 == 0
