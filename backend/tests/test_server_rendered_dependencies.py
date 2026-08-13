from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    if configured_root is not None:
        return Path(configured_root)
    return Path(__file__).resolve().parents[2]


def test_server_rendered_dependencies_are_minimal_and_locked() -> None:
    profile = _repository_root() / "profiles/server-rendered-django"
    configuration = (profile / "pyproject.toml").read_text().lower()

    assert '"django>=5.2,<6"' in configuration
    assert '"psycopg[binary]>=3.2,<4"' in configuration
    assert "redis" not in configuration
    assert "celery" not in configuration
    assert "react" not in configuration
    assert "vite" not in configuration
    assert "pnpm" not in configuration
    assert (profile / "uv.lock").is_file()


def test_server_rendered_lock_is_current() -> None:
    profile = _repository_root() / "profiles/server-rendered-django"

    subprocess.run(
        ["uv", "lock", "--check", "--project", str(profile)],
        check=True,
        capture_output=True,
        text=True,
    )
