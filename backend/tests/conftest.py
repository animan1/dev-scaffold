from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def operator_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    root = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("scaffold_operator", root / "scripts/scaffold.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def operator(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> object:
    monkeypatch.setenv("PROJECT_NAME", "dev-scaffold")
    monkeypatch.setenv("SCAFFOLD_PROFILE", "react-vite")
    monkeypatch.setenv("SCAFFOLD_BACKUP_PROFILE", "none")
    return operator_module.Operator(tmp_path)
