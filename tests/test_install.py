from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "sh" / "install.sh"
COMMON = ROOT / "sh" / "install-common.sh"


class InstallScriptTest(unittest.TestCase):
    def test_help_does_not_require_root(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/opt/mydnsdist", result.stdout)

    def test_repository_layout_is_installation_layout(self) -> None:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; source_is_complete "$2"',
                "test",
                str(COMMON),
                str(ROOT),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_does_not_depend_on_git(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("wget", content)
        self.assertIn("curl", content)
        self.assertNotIn("git clone", content)


if __name__ == "__main__":
    unittest.main()
