from __future__ import annotations

from django.test import Client
from django.test.utils import override_settings

from app.project.release_health import ReleaseHealth


@override_settings(
    DEPLOYMENT_ENVIRONMENT="staging",
    RELEASE_REVISION="a" * 40,
    RELEASE_IMAGE_PREFIX="example/app",
    RELEASE_DEPLOYED_AT="2026-08-05T20:00:00Z",
)
def test_health_endpoint() -> None:
    c = Client()
    resp = c.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "environment": "staging",
        "revision": "a" * 40,
        "imagePrefix": "example/app",
        "deployedAt": "2026-08-05T20:00:00Z",
    }
    assert ReleaseHealth.from_payload(resp.json()).payload() == resp.json()
