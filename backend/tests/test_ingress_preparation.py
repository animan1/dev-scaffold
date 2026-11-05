from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("service", ["nginx", "web"])
@pytest.mark.parametrize(
    "binding,port,hosts,csrf,public,accepted",
    [
        ("127.0.0.1", "18080", "api.example", "", "api.example", True),
        ("127.0.0.1", "18080", "api.example", "https://*.client.example:8443", "", True),
        ("::1", "18080", ".example", "https://frontend.example", "api.example", True),
        ("127.0.0.1", "18080", "[::1]", "https://[::1]", "[::1]", True),
        ("0.0.0.0", "18080", "api.example", "", "", False),
        ("127.0.0.1", "0", "api.example", "", "", False),
        ("127.0.0.1", "18080", "https://api.example", "", "", False),
        ("127.0.0.1", "18080", "api.example", "", "other.example", False),
        ("127.0.0.1", "18080", "api.example", "http://api.example", "", False),
        ("127.0.0.1", "18080", "api.example", "https://api.example/path", "", False),
        ("127.0.0.1", "18080", "api.example", "https://private:secret@api.example", "", False),
    ],
)
def test_ingress_contract_for_both_origin_services(
    service: str,
    binding: str,
    port: str,
    hosts: str,
    csrf: str,
    public: str,
    accepted: bool,
) -> None:
    root = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
    config = {
        "services": {
            service: {
                "ports": [
                    {"host_ip": binding, "published": port, "protocol": "tcp"},
                ]
            }
        }
    }
    application = {"DJANGO_ALLOWED_HOSTS": hosts, "DJANGO_CSRF_TRUSTED_ORIGINS": csrf}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,runpy,sys; runpy.run_path(sys.argv[1])['validate_ingress'](*json.load(sys.stdin))",
            str(root / "scripts/validate_prod_config.py"),
        ],
        input=json.dumps([config, application]),
        text=True,
        capture_output=True,
        env={**os.environ, "HOST_INGRESS_HOST": public},
    )
    assert (result.returncode == 0) is accepted
    assert "private" not in result.stdout + result.stderr
    assert "secret" not in result.stdout + result.stderr
