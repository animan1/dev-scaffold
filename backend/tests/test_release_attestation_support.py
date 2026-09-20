from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _repository_root() -> Path:
    configured_root = os.getenv("REPO_DIR")
    return Path(configured_root) if configured_root else Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("visibility", "owner_type", "supported"),
    [
        ("private", "User", False),
        ("public", "User", True),
        ("private", "Organization", True),
        ("internal", "Organization", True),
    ],
)
def test_release_attestation_support_policy(
    visibility: str,
    owner_type: str,
    supported: bool,
) -> None:
    result = subprocess.run(
        [
            str(_repository_root() / "scripts/release-attestation-support"),
            "--visibility",
            visibility,
            "--owner-type",
            owner_type,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == str(supported).lower()


def test_release_workflow_uploads_before_optional_attestations() -> None:
    workflow = (_repository_root() / ".github/workflows/ci.yml").read_text()
    release_job = workflow.split("  immutable-release:", 1)[1]
    upload = release_job.index("Publish digest-pinned release manifest and SBOMs")
    fallback = release_job.index("Report reduced provenance fallback")
    backend_attestation = release_job.index("Attest backend SBOM")
    web_attestation = release_job.index("Attest web SBOM")
    upload_step = release_job[upload:fallback]

    assert "scripts/release-attestation-support" in release_job
    assert release_job.count("steps.provenance.outputs.supported == 'true'") == 2
    assert "steps.provenance.outputs.supported != 'true'" in release_job
    assert "${{ env.RELEASE_FILE }}" in upload_step
    assert "${{ env.RELEASE_BACKEND_SBOM }}" in upload_step
    assert "${{ env.RELEASE_WEB_SBOM }}" in upload_step
    assert "steps.provenance.outputs.supported" not in upload_step
    assert upload < fallback < backend_attestation < web_attestation
    assert release_job.count("uses: actions/attest@v4") == 2
    assert release_job.count("uses: actions/upload-artifact@v4") == 1
