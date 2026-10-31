import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import test_release_bundle_source as history
from test_release_bundle_source import git

preserved_source = history.preserved_source

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def test_preserve_source_archives_exact_commit_and_history_without_changing_checkout(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, revision = preserved_source
    head_before = git("rev-parse", "HEAD", cwd=repository).decode().strip()
    refs_before = git("show-ref", cwd=repository)
    (repository / "deployment.conf").write_text("uncommitted working-tree edit\n")
    (repository / "untracked.txt").write_text("must not enter release source\n")
    output = work / "packaged"
    output.mkdir()

    source = bundle.preserve_source(repository, revision, output)

    assert git("rev-parse", "HEAD", cwd=repository).decode().strip() == head_before
    assert (source / "deployment.conf").read_text() == "exact committed deployment input\n"
    assert not (source / "untracked.txt").exists()
    assert git("rev-parse", "HEAD", cwd=source).decode().strip() == revision
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=source).strip() == b"HEAD"
    assert (output / "source.tar").read_bytes() == git(
        "archive", "--format=tar", revision, cwd=repository
    )
    assert git("show-ref", cwd=repository) == refs_before
    bundle.verify_source(output, revision)


def test_preserve_source_rejects_abbreviated_revision_before_cloning(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, revision = preserved_source
    output = work / "packaged"
    output.mkdir()

    with pytest.raises(bundle.Rejected, match="full 40-character revision"):
        bundle.preserve_source(repository, revision[:12], output)

    assert not (output / "history.git").exists()


def test_preserve_source_rejects_commit_missing_from_local_history(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, _ = preserved_source
    (repository / "deployment.conf").write_text("later commit\n")
    git("commit", "-am", "Later local commit", cwd=repository)
    head = git("rev-parse", "HEAD", cwd=repository).decode().strip()
    output = work / "packaged"
    output.mkdir()

    with pytest.raises(subprocess.CalledProcessError):
        bundle.preserve_source(repository, "b" * 40, output)

    assert git("rev-parse", "HEAD", cwd=repository).decode().strip() == head


def test_preserve_source_keeps_requested_commit_without_unrelated_branch_content(
    preserved_source: tuple[Path, Path, str],
) -> None:
    repository, work, revision = preserved_source
    git("checkout", "-b", "unrelated", cwd=repository)
    (repository / "unrelated.txt").write_text("unrelated branch\n")
    git("add", "unrelated.txt", cwd=repository)
    git("commit", "-m", "Unrelated branch", cwd=repository)
    output = work / "packaged"
    output.mkdir()

    source = bundle.preserve_source(repository, revision, output)

    assert git("cat-file", "-e", revision, cwd=source) == b""
    assert not (source / "unrelated.txt").exists()
    assert "refs/remotes/origin/emergency-release" in git("show-ref", cwd=source).decode()
