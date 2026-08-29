from __future__ import annotations

import subprocess
import tempfile
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

    def test_installer_only_suggests_manual_dependency_installation(self) -> None:
        content = "\n".join(
            (
                INSTALLER.read_text(encoding="utf-8"),
                COMMON.read_text(encoding="utf-8"),
            )
        )
        self.assertIn("请手动执行 apt-get update", content)
        self.assertNotIn("DEBIAN_FRONTEND=noninteractive", content)
        self.assertNotRegex(content, r"(?m)^\s*apt-get (?:update|install)\b")

    def test_existing_complete_installation_is_refreshable(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("deploy_source_tree", content)
        self.assertIn("检测到已有安装", content)
        self.assertIn("deployment_previous_root", content)

    def test_legacy_project_unit_is_replaced_by_managed_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.service"
            target = root / "dnsdist-domain-update.service"
            source.write_text("new unit\n", encoding="utf-8")
            target.write_text(
                "EnvironmentFile=-/etc/default/dnsdist-automation\n"
                "ExecStart=/usr/local/sbin/update-dnsdist-domains.py\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; safe_managed_link "$2" "$3"; readlink "$3"',
                    "test",
                    str(COMMON),
                    str(source),
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), source)

    def test_unrelated_unit_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.service"
            target = root / "dnsdist-domain-update.service"
            source.write_text("new unit\n", encoding="utf-8")
            target.write_text("custom unit\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; safe_managed_link "$2" "$3"',
                    "test",
                    str(COMMON),
                    str(source),
                    str(target),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "custom unit\n")

    def test_dns_listener_conflict_detection(self) -> None:
        def conflicts(local_address: str, process_info: str = "") -> bool:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; dns_listener_conflicts "$2" "$3" "$4"',
                    "test",
                    str(COMMON),
                    "10.133.0.1",
                    local_address,
                    process_info,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        self.assertTrue(conflicts("*:53", 'users:(("AdGuardHome",pid=1,fd=2))'))
        self.assertTrue(conflicts("10.133.0.1:53", ""))
        self.assertFalse(conflicts("10.133.0.2:53", ""))
        self.assertFalse(
            conflicts("10.133.0.1:53", 'users:(("dnsdist",pid=1,fd=2))')
        )


if __name__ == "__main__":
    unittest.main()
