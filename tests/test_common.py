from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sh"))

from dnsdist_automation.common import activate_generated_file  # noqa: E402


class ActivationTest(unittest.TestCase):
    def test_unchanged_file_does_not_run_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rules.lua"
            target.write_text("same\n", encoding="utf-8")
            commands: list[tuple[str, ...]] = []

            changed = activate_generated_file(
                target,
                "same\n",
                Path(directory) / "dnsdist.conf",
                runner=lambda command: commands.append(tuple(command)),
            )
            self.assertFalse(changed)
            self.assertEqual(commands, [])

    def test_validation_failure_restores_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rules.lua"
            target.write_text("old\n", encoding="utf-8")

            def fail_validation(command: tuple[str, ...]) -> None:
                raise RuntimeError(f"failed: {command}")

            with self.assertRaises(RuntimeError):
                activate_generated_file(
                    target,
                    "new\n",
                    Path(directory) / "dnsdist.conf",
                    runner=fail_validation,
                )
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
