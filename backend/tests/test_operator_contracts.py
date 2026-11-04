from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def release() -> dict[str, object]:
    return {
        "schema": 1,
        "revision": "a" * 40,
        "profile": "react-vite",
        "backup_profile": "none",
        "platform": "linux/arm64",
        "images": {name: "sha256:" + "b" * 64 for name in ("backend", "web", "db", "backup")},
    }


@pytest.mark.parametrize(
    "failure", ["architecture", "missing-runtime", "mutable-tag", "revision", "profile"]
)
def test_release_preflight_rejects_invalid_inventory(
    operator_module: ModuleType, failure: str
) -> None:
    data = release()
    images = data["images"]
    assert isinstance(images, dict)
    if failure == "architecture":
        data["platform"] = "linux/amd64"
    elif failure == "missing-runtime":
        del images["db"]
    elif failure == "mutable-tag":
        images["web"] = "app:latest"
    elif failure == "revision":
        data["revision"] = "main"
    else:
        data["profile"] = "other"
    with pytest.raises(operator_module.Rejected):
        operator_module.validate_release(data, "react-vite", "linux/arm64")


def test_atomic_configuration_is_private_and_literal(
    operator_module: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "private.env"
    operator_module.atomic(path, "PASSWORD='literal$credential`not-a-command`'\n")
    assert path.stat().st_mode & 0o077 == 0
    assert operator_module.read_env(path) == {"PASSWORD": "literal$credential`not-a-command`"}


def test_output_symlink_is_rejected_without_overwriting_target(
    operator_module: ModuleType, tmp_path: Path
) -> None:
    original = tmp_path / "original"
    original.write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(operator_module.Rejected, match="symlink"):
        operator_module.atomic(link, "replace")
    assert original.read_text() == "preserve"


def test_operator_lock_excludes_simultaneous_updates(
    operator_module: ModuleType, tmp_path: Path
) -> None:
    with operator_module.locked(tmp_path):
        with pytest.raises(operator_module.Rejected, match="another operation"):
            with operator_module.locked(tmp_path):
                pytest.fail("acquired another operator's lock")


@pytest.mark.parametrize(
    "goal, expected",
    [
        ("backup-test-prod", ("backup-test", "prod", None)),
        ("up-ephemeral-db", ("up", "ephemeral", "db")),
        ("download-latest", ("download-latest", None, None)),
        ("ci-ephemeral", ("ci", "ephemeral", None)),
    ],
)
def test_command_names_are_unambiguous(
    operator_module: ModuleType, goal: str, expected: tuple[str, str | None, str | None]
) -> None:
    assert operator_module.target(goal) == expected


@pytest.mark.parametrize(
    "goal", ["up", "prod-shell", "ci-prod", "restore-prod-db", "up-prod-../db"]
)
def test_invalid_command_names_are_rejected(operator_module: ModuleType, goal: str) -> None:
    with pytest.raises(operator_module.Rejected):
        operator_module.target(goal)


def test_changed_coverage_fails_even_when_total_coverage_would_pass(tmp_path: Path) -> None:
    root = Path(os.getenv("REPO_DIR", Path(__file__).resolve().parents[2]))
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
            str(root / "scripts/changed_coverage.py"),
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


def test_incomplete_cached_bundle_never_accesses_publication(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    monkeypatch.setattr(op, "head", lambda: "a" * 40)
    monkeypatch.setattr(op, "architecture", lambda: "linux/arm64")
    op.release_dir().mkdir(parents=True)
    monkeypatch.setattr(
        operator_module, "run", lambda *a, **k: pytest.fail("unexpected network or Docker access")
    )
    with pytest.raises(operator_module.Rejected, match="integrity.json"):
        op.download()


def test_sealed_bundle_corruption_is_rejected_before_docker_access(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    monkeypatch.setattr(op, "head", lambda: "a" * 40)
    monkeypatch.setattr(op, "architecture", lambda: "linux/arm64")
    directory = op.release_dir()
    directory.mkdir(parents=True)
    for name in ("manifest.json", "source.tar", "images.tar"):
        (directory / name).write_bytes(b"original")
    operator_module.seal_release(directory)
    (directory / "images.tar").write_bytes(b"corrupt")
    monkeypatch.setattr(
        operator_module, "run", lambda *a, **k: pytest.fail("unexpected Docker access")
    )
    with pytest.raises(operator_module.Rejected, match="integrity mismatch"):
        op.download()


def test_resolved_credentials_escape_only_compose_interpolation(
    operator_module: ModuleType,
) -> None:
    secret = "literal$cash${NOT_AN_INPUT}`command`"
    model = {"environment": {"PASSWORD": secret}}
    encoded = operator_module.compose_literals(model)
    assert model["environment"]["PASSWORD"] == secret
    assert encoded["environment"]["PASSWORD"] == secret.replace("$", "$$")
