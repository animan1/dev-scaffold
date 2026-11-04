import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("url", "engine", "name"),
    [
        ("postgresql://app:unused@unused/app", "django.db.backends.postgresql", "app"),
        (
            "sqlite:////tmp/source-check.sqlite3",
            "django.db.backends.sqlite3",
            "/tmp/source-check.sqlite3",
        ),
        ("", "django.db.backends.sqlite3", "db.sqlite3"),
    ],
)
def test_development_database_follows_explicit_url(url: str, engine: str, name: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from django.conf import settings; print(json.dumps(settings.DATABASES['default']))",
        ],
        env={
            "DATABASE_URL": url,
            "DJANGO_SETTINGS_MODULE": "app.project.settings.dev",
            "DJANGO_SECRET_KEY": "settings-only-test-secret",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    configured = json.loads(result.stdout)
    assert configured["ENGINE"] == engine
    assert str(configured["NAME"]).endswith(name)
