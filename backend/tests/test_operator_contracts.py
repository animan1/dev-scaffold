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
    for name in ("manifest.json", "source.tar", "source.bundle", "images.tar.gz"):
        (directory / name).write_bytes(b"original")
    operator_module.seal_release(directory)
    (directory / "images.tar.gz").write_bytes(b"corrupt")
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


def test_image_capture_failure_preserves_previous_archive(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = tmp_path / "docker"
    command.write_text("#!/bin/sh\nprintf partial\nexit 1\n")
    command.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    archive = tmp_path / "images.tar.gz"
    archive.write_bytes(b"previous")
    with pytest.raises(operator_module.Rejected, match="capture failed"):
        operator_module.save_images(archive, ["sha256:" + "a" * 64])
    assert archive.read_bytes() == b"previous"
    assert not archive.with_suffix(".gz.pending").exists()


def test_image_checks_never_prepare_or_render_an_environment(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    data = release()
    monkeypatch.setattr(op, "release", lambda: data)
    monkeypatch.setattr(op, "release_dir", lambda: tmp_path)
    monkeypatch.setattr(operator_module, "verify_seal", lambda *a: None)
    monkeypatch.setattr(operator_module, "seal_release", lambda *a: None)
    monkeypatch.setattr(operator_module, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        op,
        "inspect_image",
        lambda *a: {"Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}},
    )
    monkeypatch.setattr(
        op, "prepare", lambda *a: pytest.fail("image validation prepared an environment")
    )
    monkeypatch.setattr(
        op, "config", lambda *a: pytest.fail("image validation rendered an environment")
    )
    op.image_check()
    assert operator_module.read_json(tmp_path / "image-check.json")["revision"] == "a" * 40


def test_newest_backup_metadata_failure_invalidates_prior_selection_without_fallback(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    op = operator_module.Operator(tmp_path)
    repository = tmp_path / "backups"
    repository.mkdir()
    output = op.local / "recovery.env"
    operator_module.atomic(output, "stale selection\n")
    monkeypatch.setenv("SELECT", "newest")
    monkeypatch.setenv("ENV_FILE", ".local/recovery.env")
    monkeypatch.setattr(
        op,
        "settings",
        lambda *a: {"BACKUP_PASSWORD": "test-key", "BACKUP_LOCAL_PATH": str(repository)},
    )
    monkeypatch.setattr(op, "state_dir", lambda *a: tmp_path / "state")
    snapshots = [
        {
            "time": "2026-01-01T00:00:00Z",
            "id": "a" * 64,
            "tags": ["release-revision:" + "a" * 40, "release-identity:" + "b" * 64],
        },
        {"time": "2026-01-02T00:00:00Z", "id": "c" * 64, "tags": []},
    ]
    monkeypatch.setattr(operator_module, "run", lambda *a, **k: json.dumps(snapshots))
    with pytest.raises(operator_module.Rejected, match="newest snapshot"):
        op.snapshots("prod")
    assert not output.exists()


def test_prepared_setup_never_rebuilds_valid_tools(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    op = operator_module.Operator(tmp_path)
    monkeypatch.setattr(op, "head", lambda: "a" * 40)
    monkeypatch.setattr(op, "architecture", lambda: "linux/arm64")
    monkeypatch.setattr(op, "tooling_inputs", lambda *a: "inputs")
    monkeypatch.setattr(op, "backup_inputs", lambda: "inputs")
    monkeypatch.setattr(
        op,
        "inspect_image",
        lambda *a: {"Config": {"Labels": {"io.scaffold.tooling-inputs": "inputs"}}},
    )
    commands = []
    monkeypatch.setattr(operator_module, "run", lambda *a, **k: commands.append(a))
    op.setup()
    assert not any(command[:2] == ("docker", "build") for command in commands)


@pytest.mark.parametrize("mismatch", [False, True])
def test_database_inputs_resolve_references_without_rewriting_credentials(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: bool
) -> None:
    op = operator_module.Operator(tmp_path)
    path = tmp_path / "private.env"
    original = "DJANGO_SECRET_KEY=private\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=literal$@/secret\nPOSTGRES_DB=app\nBACKUP_PASSWORD=backup-key\nDATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}\n"
    path.write_text(original.replace("@db:5432/", "@other:5432/") if mismatch else original)
    monkeypatch.setattr(op, "inputs", lambda *a: path)
    if mismatch:
        with pytest.raises(operator_module.Rejected, match="DATABASE_URL must match"):
            op.settings("prod")
    else:
        values = op.settings("prod")
        assert values["POSTGRES_PASSWORD"] == "literal$@/secret"
        assert values["DATABASE_URL"] == "postgresql://app:literal%24%40%2Fsecret@db:5432/app"
        assert path.read_text() == original


def test_cross_environment_restore_keeps_source_read_only_and_target_credentials(
    operator_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import copy

    op = operator_module.Operator(tmp_path)
    source = {
        "services": {
            "backup": {
                "environment": {
                    "POSTGRES_PASSWORD": "production-db",
                    "RESTIC_PASSWORD": "production-key",
                    "RESTIC_REPOSITORY": "/backups",
                    "BACKUP_HOST": "production",
                },
                "volumes": [
                    {"type": "bind", "source": "/production/backups", "target": "/backups"}
                ],
            }
        },
        "volumes": {},
    }
    destination = {
        "services": {
            "backup": {
                "environment": {
                    "POSTGRES_PASSWORD": "disposable-db",
                    "RESTIC_PASSWORD": "disposable-key",
                },
                "volumes": [{"type": "volume", "source": "status", "target": "/backup-status"}],
            }
        },
        "volumes": {"status": {}},
    }
    original = copy.deepcopy(source)
    monkeypatch.setattr(op, "release", lambda: {})
    monkeypatch.setattr(op, "config", lambda *a: (None, copy.deepcopy(destination)))
    monkeypatch.setattr(op, "current_model", lambda *a: (None, source))
    calls = []
    monkeypatch.setattr(
        op,
        "compose_model",
        lambda env, model, *args, **kwargs: calls.append((copy.deepcopy(model), args)),
    )
    op.backup("ephemeral", "restore", "a" * 64, capture=True, source="prod")
    assert source == original
    service = calls[-1][0]["services"]["backup"]
    assert service["environment"]["POSTGRES_PASSWORD"] == "disposable-db"
    assert service["environment"]["RESTIC_PASSWORD"] == "production-key"
    assert service["environment"]["BACKUP_READ_ONLY"] == "1"
    assert service["volumes"][-1]["read_only"] is True
    assert calls[0][1][-1].endswith(" /backup-status")
