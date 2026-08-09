from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_host_ingress_origin_is_loopback_http_only() -> None:
    compose = (REPO_ROOT / "deploy/docker-compose.host-ingress.yml").read_text()
    nginx = (REPO_ROOT / "deploy/nginx/default.host-ingress.conf").read_text()

    assert '"127.0.0.1:${HOST_INGRESS_PORT:-18080}:80"' in compose
    assert "/etc/nginx/certs" not in compose
    assert "listen 80;" in nginx
    assert "listen 443" not in nginx
    assert "ssl_certificate" not in nginx


def test_host_ingress_normalizes_forwarding_headers_at_the_proxy_boundary() -> None:
    nginx = (REPO_ROOT / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "map $http_x_forwarded_proto $trusted_forwarded_proto" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto;" in nginx
