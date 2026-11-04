from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))


@pytest.fixture
def source_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "source_commands", ROOT / "scripts/scaffold_source.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_source_tests_use_isolated_tools_and_never_compose(
    source_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", profile)
    monkeypatch.setenv("COVERAGE", "1")
    op = source_module.SourceOperator(tmp_path)
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "execute", lambda *a, **k: commands.append(a))
    monkeypatch.setattr(op, "architecture", lambda: "linux/arm64")
    monkeypatch.setattr(op, "tooling_inputs", lambda *a: "prepared-inputs")
    monkeypatch.setattr(op, "git", lambda *a: str(tmp_path / ".git"))
    monkeypatch.setattr(
        op,
        "inspect_image",
        lambda *a: {"Config": {"Labels": {"io.scaffold.tooling-inputs": "prepared-inputs"}}},
    )
    op.gate("test")
    containers = [command for command in commands if command[:2] == ("docker", "run")]
    assert len(containers) == (2 if profile == "react-vite" else 1)
    for command in containers:
        assert "--pull=never" in command
        assert "--user" in command
        assert "DATABASE_URL=sqlite:////tmp/scaffold-source-check.sqlite3" in command
        assert "compose" not in command
    backend = containers[0]
    assert "pytest" in backend
    assert "--cov=src/app" in backend
    assert "--cov-report=xml" in backend
    assert commands[-1][1] == tmp_path / "scripts/changed_coverage.py"
    if profile == "react-vite":
        assert "--coverage.include=src/**/*.{ts,tsx}" in containers[1]


def test_source_entrypoint_rejects_deployment_operations(
    source_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = source_module.SourceOperator(tmp_path)
    monkeypatch.setattr(op, "execute", lambda *a, **k: pytest.fail("deployment reached execution"))
    with pytest.raises(source_module.Rejected, match="unknown source command"):
        op.dispatch("up-prod")
    assert not op.local.exists()


def test_setup_keeps_regenerated_frontend_dependencies_owned_by_the_host_user(
    source_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", "react-vite")
    op = source_module.SourceOperator(tmp_path)
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(op, "prepared_tool", lambda *a: True)
    monkeypatch.setattr(op, "tooling_inputs", lambda *a: "prepared-inputs")
    monkeypatch.setattr(op, "execute", lambda *a, **k: commands.append(a))
    op.setup()
    assert len(commands) == 2
    assert "0:0" in commands[0]
    assert "-I" in commands[0] and "-S" in commands[0]
    assert "db.sqlite3" not in str(commands[0])
    assert commands[-1][:2] == ("docker", "run")
    assert "--user" in commands[-1]
    assert "--no-preserve=ownership" in commands[-1]


def test_changed_coverage_fails_even_when_total_coverage_would_pass(tmp_path: Path) -> None:
    source = tmp_path / "backend/src/app/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("def answer():\n    return 1\n")
    for command in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "backend/src/app/example.py"],
        ["git", "commit", "--quiet", "-m", "baseline"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    source.write_text("def answer():\n    return 2\n")
    coverage = tmp_path / "coverage.xml"
    coverage.write_text(
        '<coverage line-rate="0.99"><packages><package><classes><class filename="app/example.py"><lines><line number="1" hits="1"/><line number="2" hits="0"/></lines></class></classes></package></packages></coverage>'
    )
    result = subprocess.run(
        [
            "python",
            str(ROOT / "scripts/changed_coverage.py"),
            "--root",
            str(tmp_path),
            "--base",
            "HEAD",
            "--xml",
            str(coverage),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "backend/src/app/example.py:2" in result.stderr
