import importlib.util
import json
import os
import tarfile
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

REVISION = "a" * 40
REPOSITORY = "animan1/example-app"


def _set_selection(monkeypatch: pytest.MonkeyPatch, artifact: str) -> None:
    monkeypatch.setenv("BUNDLE_REPO", REPOSITORY)
    monkeypatch.setenv("BUNDLE_RUN_ID", "12345")
    monkeypatch.setenv("BUNDLE_ARTIFACT", artifact)


def _run_metadata(conclusion: str = "success") -> dict[str, object]:
    return {
        "head_sha": REVISION,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": conclusion,
        "repository": {"full_name": REPOSITORY},
    }


def _install_gh_fixture(
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
    metadata: dict[str, object] | None = None,
    merge_base: str = REVISION,
    missing: str = "",
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if args[:2] == ("gh", "api") and "/actions/runs/" in args[2]:
            return json.dumps(metadata or _run_metadata()).encode()
        if args[:2] == ("gh", "api"):
            return json.dumps({"merge_base_commit": {"sha": merge_base}}).encode()
        assert args[:3] == ("gh", "run", "download")
        artifact_dir = work / "artifacts"
        artifact_dir.mkdir()
        if missing != "manifest":
            (artifact_dir / f"{REVISION}.env").write_text(f"RELEASE_REVISION={REVISION}\n")
        if missing != "api":
            (artifact_dir / f"{REVISION}-backend.spdx.json").write_text('{"spdxVersion":"2.3"}')
        if missing != "web":
            (artifact_dir / f"{REVISION}-web.spdx.json").write_text('{"spdxVersion":"2.3"}')
        return b""

    monkeypatch.setattr(bundle, "run", fake_run)
    return calls


def test_capture_requires_a_successful_selected_main_run_and_packs_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = f"release-{REVISION}-arm64"
    _set_selection(monkeypatch, artifact)
    calls = _install_gh_fixture(monkeypatch, tmp_path)

    manifest, evidence = bundle.capture_evidence(
        tmp_path,
        REVISION,
        "linux/arm64",
        {"GH_TOKEN": "private"},
        {"images": {"app": "RELEASE_BACKEND_IMAGE", "web": "RELEASE_WEB_IMAGE"}},
    )

    assert manifest.name == f"{REVISION}.env"
    assert evidence["source"] == "github-main"
    assert evidence["repository"] == REPOSITORY
    assert evidence["run_id"] == "12345"
    assert evidence["artifact"] == artifact
    assert calls == [
        ("gh", "api", f"repos/{REPOSITORY}/actions/runs/12345"),
        ("gh", "api", f"repos/{REPOSITORY}/compare/{REVISION}...main"),
        (
            "gh",
            "run",
            "download",
            "12345",
            "--repo",
            REPOSITORY,
            "--name",
            artifact,
            "--dir",
            str(tmp_path / "artifacts"),
        ),
    ]
    with tarfile.open(tmp_path / "artifacts.tar") as archive:
        assert set(archive.getnames()) == {
            f"{REVISION}.env",
            f"{REVISION}-backend.spdx.json",
            f"{REVISION}-web.spdx.json",
        }


def test_capture_rejects_unsuccessful_or_wrong_revision_run_before_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_selection(monkeypatch, f"release-{REVISION}-arm64")
    calls = _install_gh_fixture(monkeypatch, tmp_path, metadata=_run_metadata(conclusion="failure"))

    with pytest.raises(bundle.Rejected, match="successful main release"):
        bundle.capture_evidence(
            tmp_path,
            REVISION,
            "linux/arm64",
            {},
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
        )

    assert len(calls) == 1
    assert calls[0][:2] == ("gh", "api")


def test_capture_requires_revision_to_remain_on_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_selection(monkeypatch, f"release-{REVISION}-arm64")
    calls = _install_gh_fixture(monkeypatch, tmp_path, merge_base="b" * 40)

    with pytest.raises(bundle.Rejected, match="no longer on main"):
        bundle.capture_evidence(
            tmp_path,
            REVISION,
            "linux/arm64",
            {},
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
        )

    assert len(calls) == 2
    assert calls[-1][2].endswith("/compare/" + REVISION + "...main")


@pytest.mark.parametrize("missing", ["manifest", "web"])
def test_capture_rejects_missing_manifest_or_sbom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    _set_selection(monkeypatch, f"release-{REVISION}-arm64")
    _install_gh_fixture(monkeypatch, tmp_path, missing=missing)
    expected_error = bundle.Rejected if missing == "manifest" else FileNotFoundError
    with pytest.raises(expected_error):
        bundle.capture_evidence(
            tmp_path,
            REVISION,
            "linux/arm64",
            {},
            {
                "images": (
                    {"app": "RELEASE_BACKEND_IMAGE"}
                    if missing == "manifest"
                    else {"web": "RELEASE_WEB_IMAGE"}
                )
            },
        )


def test_capture_rejects_legacy_artifact_for_arm64(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_selection(monkeypatch, f"release-{REVISION}")

    with pytest.raises(bundle.Rejected, match="AMD64 only"):
        bundle.capture_evidence(
            tmp_path,
            REVISION,
            "linux/arm64",
            {},
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
        )


def test_capture_rejects_a_run_from_another_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_selection(monkeypatch, f"release-{REVISION}-arm64")
    metadata = _run_metadata()
    metadata["repository"] = {"full_name": "another/repository"}
    calls = _install_gh_fixture(monkeypatch, tmp_path, metadata=metadata)

    with pytest.raises(bundle.Rejected, match="successful main release"):
        bundle.capture_evidence(
            tmp_path,
            REVISION,
            "linux/arm64",
            {},
            {"images": {"app": "RELEASE_BACKEND_IMAGE"}},
        )

    assert len(calls) == 1
