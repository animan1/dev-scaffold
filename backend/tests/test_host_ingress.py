from __future__ import annotations

import os
import subprocess
from pathlib import Path

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
    assert nginx.count("proxy_pass http://${BACKEND_HOST}:8000;") == 2
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


def test_production_workflows_refresh_nginx_after_backend_replacement() -> None:
    makefile = (_repo_root() / "Makefile").read_text()

    refresh = "$(COMPOSE_PROD) up -d --no-deps --force-recreate nginx"
    assert makefile.count(refresh) == 2
    assert "$(COMPOSE_RELEASE) up -d --no-deps --force-recreate nginx" in makefile


def test_host_ingress_normalizes_forwarding_headers_at_the_proxy_boundary() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "map $http_x_forwarded_proto $trusted_forwarded_proto" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto;" in nginx


def test_local_tls_preparation_requires_opt_in_and_covers_browser_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", "server-rendered-django")
    (tmp_path / "deploy").mkdir()
    private_env = tmp_path / "deploy/.env.prod"
    private_env.write_text("existing-private-configuration\n")
    command = [
        "make",
        "--no-print-directory",
        "-f",
        str(_repo_root() / "Makefile"),
        "SCAFFOLD_PROFILE=react-vite",
        "prepare-prod",
    ]
    rejected = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert not (tmp_path / "deploy/nginx/certs/server.key").exists()
    subprocess.run([*command, "LOCAL_TLS=1"], cwd=tmp_path, capture_output=True, check=True)
    certificate = tmp_path / "deploy/nginx/certs/server.crt"
    inspected = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(certificate),
            "-noout",
            "-ext",
            "subjectAltName",
            "-checkend",
            "86400",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "DNS:localhost" in inspected.stdout
    assert "IP Address:127.0.0.1" in inspected.stdout
    assert private_env.read_text() == "existing-private-configuration\n"
    assert (tmp_path / "deploy/nginx/certs/server.key").stat().st_mode & 0o077 == 0
