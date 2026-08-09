from __future__ import annotations

import os
from pathlib import Path


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


def test_host_ingress_normalizes_forwarding_headers_at_the_proxy_boundary() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "map $http_x_forwarded_proto $trusted_forwarded_proto" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto;" in nginx
