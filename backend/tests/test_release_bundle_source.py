import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def git(*args: str, cwd: Path) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


@pytest.fixture
def preserved_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    repository = tmp_path / "repository"
    repository.mkdir()
    git("init", "-b", "hotfix", cwd=repository)
    git("config", "user.name", "Release Test", cwd=repository)
    git("config", "user.email", "release@example.invalid", cwd=repository)
    (repository / "deployment.conf").write_text("exact committed deployment input\n")
    git("add", "deployment.conf", cwd=repository)
    git("commit", "-m", "Preserved hotfix", cwd=repository)
    revision = git("rev-parse", "HEAD", cwd=repository).decode().strip()
    work = tmp_path / "payload"
    work.mkdir()
    (work / "source.tar").write_bytes(git("archive", "--format=tar", revision, cwd=repository))
    git("bundle", "create", str(work / "history.bundle"), "refs/heads/hotfix", cwd=repository)
    return repository, work, revision


def test_verifies_source_without_original_checkout(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, revision = preserved_source
    original_archive = (work / "source.tar").read_bytes()
    shutil.rmtree(repository)
    bundle.verify_source(work, revision)
    assert git("rev-parse", revision, cwd=work / "verify.git").decode().strip() == revision
    assert (work / "source.tar").read_bytes() == original_archive


def test_rejects_modified_deployment_source(
    preserved_source: tuple[Path, Path, str],
) -> None:
    _, work, revision = preserved_source
    (work / "source.tar").write_bytes(b"substituted deployment source")
    with pytest.raises(bundle.Rejected, match="differs from preserved commit"):
        bundle.verify_source(work, revision)


def test_rejects_commit_missing_from_preserved_history(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, _ = preserved_source
    (repository / "deployment.conf").write_text("later unbundled commit\n")
    git("commit", "-am", "Not in the saved bundle", cwd=repository)
    later = git("rev-parse", "HEAD", cwd=repository).decode().strip()
    with pytest.raises(subprocess.CalledProcessError):
        bundle.verify_source(work, later)


def test_rejects_abbreviated_revision_before_cloning(
    preserved_source: tuple[Path, Path, str],
) -> None:
    _, work, revision = preserved_source
    with pytest.raises(bundle.Rejected, match="full 40-character revision"):
        bundle.verify_source(work, revision[:12])
    assert not (work / "verify.git").exists()
