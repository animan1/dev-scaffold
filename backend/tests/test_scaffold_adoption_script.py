from __future__ import annotations

import subprocess
from pathlib import Path


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_adoption_helper_is_valid_guarded_bash() -> None:
    script = _repository_root() / "scripts/prepare-scaffold-adoption"
    subprocess.run(["bash", "-n", str(script)], check=True)

    text = script.read_text()
    assert "status --porcelain" in text
    assert "Destination must have a clean working tree." in text
    assert "Copy and stage non-collision paths? [y/N]" in text
    assert '--files-from="$import_tmp/import-paths"' in text
    assert '--pathspec-from-file="$import_tmp/import-paths"' in text
    assert "rm " not in text
