from __future__ import annotations

import json
import os
import re
from pathlib import Path


def root() -> Path:
    return Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))


def test_backup_image_uses_immutable_versioned_component_images() -> None:
    dockerfile = (root() / "profiles/immutable-backup/Dockerfile").read_text()
    pins = json.loads((root() / "deploy/dependencies.json").read_text())["images"]
    for name in ("postgres", "restic", "rclone"):
        assert re.fullmatch(
            re.escape(pins[name]["source"]) + r"@sha256:[0-9a-f]{64}", pins[name]["pin"]
        )
        assert name.upper() + "_IMAGE" in dockerfile


def test_backup_image_is_application_independent_and_non_root() -> None:
    dockerfile = (root() / "profiles/immutable-backup/Dockerfile").read_text()
    assert "USER scaffold-backup" in dockerfile
    assert "COPY backend" not in dockerfile
    assert "DJANGO" not in dockerfile
    assert "/var/run/docker.sock" not in dockerfile


def test_backup_image_owns_the_generic_operation_entrypoint() -> None:
    dockerfile = (root() / "profiles/immutable-backup/Dockerfile").read_text()
    assert "profiles/immutable-backup/backup.sh" in dockerfile
    assert 'ENTRYPOINT ["scaffold-backup"]' in dockerfile
    assert 'CMD ["watch"]' in dockerfile
    assert "jq" in dockerfile
