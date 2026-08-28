from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNINSTALLER = ROOT / "sh" / "uninstall.sh"


class UninstallScriptTest(unittest.TestCase):
    def run_dry(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(UNINSTALLER), "--dry-run", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_default_preserves_configuration(self) -> None:
        result = self.run_dry()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dnsdist-domain-update.timer", result.stdout)
        self.assertIn("remove-managed-link", result.stdout)
        self.assertNotIn("rm -rf", result.stdout)

    def test_purge_removes_the_central_installation_directory(self) -> None:
        result = self.run_dry("--purge")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rm -rf", result.stdout)
        self.assertIn("dnsdist", result.stdout)
        self.assertNotIn("apt-get", result.stdout)

    def test_unknown_option_is_rejected(self) -> None:
        result = self.run_dry("--unknown")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
