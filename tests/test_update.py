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

    def test_new_rules_are_generated_before_installation_swap(self) -> None:
        content = UPDATER.read_text(encoding="utf-8")
        generate = content.index(
            '"${next_root}/sh/update-dnsdist-domains.py" --no-check --no-reload'
        )
        validate = content.index(
            'dnsdist --check-config -C "${next_root}/config/dnsdist.conf"'
        )
        swap = content.index('mv "${install_dir}" "${previous_root}"')
        self.assertLess(generate, validate)
        self.assertLess(validate, swap)
        self.assertEqual(content.count("update-dnsdist-domains.py"), 1)


if __name__ == "__main__":
    unittest.main()
