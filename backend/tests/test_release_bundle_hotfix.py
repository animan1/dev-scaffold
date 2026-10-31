import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

REVISION = "a" * 40
APP_ID = "sha256:" + "1" * 64


def _install_git_fixture(
    monkeypatch: pytest.MonkeyPatch,
    revision: str = REVISION,
    status: bytes = b"",
    changed: bytes = b"",
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if args[1:3] == ("status", "--porcelain"):
            return status
        if args[1:3] == ("rev-parse", "HEAD"):
            return revision.encode()
        if args[1:3] == ("diff", "--exit-code"):
            return changed
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(bundle, "run", fake_run)
    return calls


def _arguments(tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, object]]:
    source = tmp_path / "source"
    source.mkdir()
    return (
        source,
        {"SCAFFOLD_PROFILE": "example", "RELEASE_PLATFORM": "linux/arm64"},
        {"images": {"app": "RELEASE_BACKEND_IMAGE"}, "check_targets": ["release-check"]},
    )


def test_hotfix_builds_target_platform_and_checks_same_image_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, values, spec = _arguments(tmp_path)
    root = tmp_path / "checkout"
    env = {"PATH": "/bin"}
    git_calls = _install_git_fixture(monkeypatch)
    make_calls: list[tuple[Path, str, dict[str, str], dict[str, str]]] = []

    def fake_make(
        path: Path, target: str, options: dict[str, str], environment: dict[str, str]
    ) -> None:
        make_calls.append((path, target, options.copy(), environment.copy()))

    monkeypatch.setattr(bundle, "make", fake_make)
    monkeypatch.setattr(bundle, "inspect", lambda reference, environment: {"Id": APP_ID})

    manifest, build_values, roles, evidence = bundle.build_hotfix(
        root, source, REVISION, "linux/arm64", "example", values, env, spec
    )

    assert manifest.read_text().splitlines() == [
        f"RELEASE_REVISION={REVISION}",
        "SCAFFOLD_PROFILE=example",
        f"RELEASE_IMAGE_PREFIX=local/emergency-{REVISION}",
        f"RELEASE_BACKEND_IMAGE=local/emergency-{REVISION}-backend:{REVISION}",
    ]
    assert build_values["RELEASE_IMAGE_PREFIX"] == f"local/emergency-{REVISION}"
    assert build_values["RELEASE_REVISION"] == REVISION
    assert build_values["RELEASE_PLATFORM"] == "linux/arm64"
    assert roles == {"RELEASE_BACKEND_IMAGE": f"local/emergency-{REVISION}-backend:{REVISION}"}
    assert env["DOCKER_DEFAULT_PLATFORM"] == "linux/arm64"
    assert [call[1] for call in make_calls] == ["build-release-images", "release-check"]
    assert all(call[3]["DOCKER_DEFAULT_PLATFORM"] == "linux/arm64" for call in make_calls)
    check_values = make_calls[1][2]
    assert check_values["PROJECT_NAME"] == f"emergency-check-{REVISION[:16]}"
    assert check_values["RELEASE_COMPOSE_PROJECT"] == f"emergency-check-{REVISION[:16]}-release"
    assert evidence["source"] == "local-checks"
    assert evidence["revision"] == REVISION
    assert evidence["platform"] == "linux/arm64"
    assert evidence["images"] == {"RELEASE_BACKEND_IMAGE": APP_ID}
    assert "normal PR workflow" in evidence["follow_up"]
    assert [call[1:3] for call in git_calls] == [
        ("status", "--porcelain"),
        ("rev-parse", "HEAD"),
        ("diff", "--exit-code"),
    ]


@pytest.mark.parametrize(
    "revision, status, message",
    [
        (REVISION, b" M tracked.py\n", "commit all hotfix changes first"),
        ("b" * 40, b"", "must equal committed HEAD"),
    ],
)
def test_hotfix_rejects_dirty_or_wrong_commit_before_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    revision: str,
    status: bytes,
    message: str,
) -> None:
    source, values, spec = _arguments(tmp_path)
    calls = _install_git_fixture(monkeypatch, revision=revision, status=status)
    builds: list[str] = []
    monkeypatch.setattr(bundle, "make", lambda root, target, *args: builds.append(target))

    with pytest.raises(bundle.Rejected, match=message):
        bundle.build_hotfix(
            tmp_path / "checkout",
            source,
            REVISION,
            "linux/arm64",
            "example",
            values,
            {},
            spec,
        )

    assert not builds
    expected_calls = 1 if status else 2
    assert len(calls) == expected_calls


def test_hotfix_rejects_checks_that_change_application_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, values, spec = _arguments(tmp_path)
    _install_git_fixture(monkeypatch)
    monkeypatch.setattr(bundle, "make", lambda *args: None)
    identities = iter([APP_ID, "sha256:" + "2" * 64])
    monkeypatch.setattr(bundle, "inspect", lambda reference, environment: {"Id": next(identities)})

    with pytest.raises(bundle.Rejected, match="changed the built application images"):
        bundle.build_hotfix(
            tmp_path / "checkout",
            source,
            REVISION,
            "linux/arm64",
            "example",
            values,
            {},
            spec,
        )


def test_hotfix_rejects_checks_that_change_committed_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, values, spec = _arguments(tmp_path)
    calls = _install_git_fixture(monkeypatch, changed=b"changed.py\n")
    monkeypatch.setattr(bundle, "make", lambda *args: None)
    monkeypatch.setattr(bundle, "inspect", lambda reference, environment: {"Id": APP_ID})

    with pytest.raises(bundle.Rejected, match="checks changed committed source"):
        bundle.build_hotfix(
            tmp_path / "checkout",
            source,
            REVISION,
            "linux/arm64",
            "example",
            values,
            {},
            spec,
        )

    assert calls[-1][1:3] == ("diff", "--exit-code")
