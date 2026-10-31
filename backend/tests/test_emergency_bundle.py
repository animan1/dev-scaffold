import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(os.environ.get("REPO_DIR", Path(__file__).resolve().parents[2]))
SPEC = importlib.util.spec_from_file_location("release_bundle", ROOT / "scripts/release_bundle.py")
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_install_requires_authorization_before_any_subprocess(self) -> None:
        with (
            patch.dict(os.environ, {"EMERGENCY": "0"}),
            patch.object(bundle, "run") as run,
        ):
            with self.assertRaisesRegex(bundle.Rejected, "EMERGENCY=1"):
                bundle.destination("install")
            run.assert_not_called()

    def test_default_smoke_checks_revision_through_trusted_loopback_headers(self) -> None:
        class Response(io.BytesIO):
            status = 200

        replies = [
            Response(json.dumps({"revision": "a" * 40}).encode()),
            Response(b"root"),
            Response(b"static"),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "BUNDLE_SMOKE_URL": "http://127.0.0.1:18080",
                    "RELEASE_REVISION": "a" * 40,
                },
            ),
            patch.object(bundle.urllib.request, "urlopen", side_effect=replies) as urlopen,
        ):
            bundle.smoke()
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("X-forwarded-proto"), "https")
        self.assertEqual(request.full_url, "http://127.0.0.1:18080/api/healthz")

    def test_json_duplicate_keys_and_manifest_expansion_are_rejected(self) -> None:
        path = self.root / "bad.json"
        path.write_text('{"schema":1,"schema":2}')
        with self.assertRaises(bundle.Rejected):
            bundle.read_json(path)
        path.write_text("RELEASE_REVISION=$(touch /tmp/unwanted)\n")
        with self.assertRaises(bundle.Rejected):
            bundle.manifest_values(path)


@pytest.mark.skipif(
    os.environ.get("RUN_EMERGENCY_INTEGRATION") != "1",
    reason="opt-in: privileged Docker engines and initial registry access required",
)
def test_isolated_emergency_operator_workflows() -> None:
    subprocess.run([sys.executable, str(ROOT / "tests/emergency_release/exercise.py")], check=True)
