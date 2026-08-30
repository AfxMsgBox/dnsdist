from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "sh" / "update.sh"


class UpdateScriptTest(unittest.TestCase):
    def test_help_does_not_require_root(self) -> None:
        result = subprocess.run(
            ["bash", str(UPDATER), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--configure", result.stdout)

    def test_update_uses_archive_checksum_and_config_merge(self) -> None:
        content = UPDATER.read_text(encoding="utf-8")
        self.assertIn(".source-sha256", content)
        self.assertIn("manage-config.py", content)
        self.assertIn("previous_root", content)
        self.assertIn('remove_development_files "${next_root}"', content)
        self.assertNotIn("unittest discover", content)
        self.assertNotIn("git pull", content)


if __name__ == "__main__":
    unittest.main()
