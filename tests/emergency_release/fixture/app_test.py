from pathlib import Path
import unittest


class ApplicationTest(unittest.TestCase):
    def test_revision_content_is_present(self):
        self.assertIn(
            Path("revision.txt").read_text().strip(), ("release-one", "hotfix-two")
        )
