from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

import pytest


def test_server_rendered_production_security_fails_closed() -> None:
    root = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))
    settings = (root / "backend/src/app/project/settings/prod.py").read_text()
    for name in ("SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"):
        assert name + " = True" in settings
        assert 'env.bool("DJANGO_' + name not in settings


def test_server_rendered_profile_exposes_django_admin() -> None:
    root = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))
    urls = (root / "backend/src/app/project/urls.py").read_text()
    assert 'path("admin/", admin.site.urls)' in urls


@pytest.mark.parametrize("gate", ["format-check", "lint", "typecheck", "test"])
def test_server_rendered_checks_use_its_committed_configuration(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", "server-rendered-django")
    monkeypatch.setenv("COVERAGE", "0")
    op = operator_module.Operator(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "tool", lambda *a, **k: calls.append(a))
    op.gate(gate)
    configured = [call for call in calls if call[0] in ("ruff", "mypy", "pytest")]
    assert len(configured) == 1
    assert any(
        "profiles/server-rendered-django/pyproject.toml" in str(value) for value in configured[0]
    )
    if gate == "lint":
        assert ("python", "scripts/check_no_any.py") in calls


def test_source_gate_composition_stops_at_the_first_failure(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    calls: list[str] = []

    def gate(name: str) -> None:
        calls.append(name)
        if name == "lint":
            raise operator_module.Rejected("lint failed")

    monkeypatch.setattr(op, "gate", gate)
    with pytest.raises(operator_module.Rejected, match="lint failed"):
        op.check()
    assert calls == ["format-check", "lint"]
