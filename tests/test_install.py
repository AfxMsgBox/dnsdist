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
        self.assertIn("--dns-port", result.stdout)

    def test_invalid_dns_port_is_rejected_before_installation(self) -> None:
        for value in ("0", "65536", "not-a-port"):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["bash", str(INSTALLER), "--dns-port", value],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("1 到 65535", result.stderr)

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

    def test_installer_does_not_run_or_deploy_development_tests(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        common = COMMON.read_text(encoding="utf-8")
        self.assertNotIn("python3 -m unittest", installer + common)
        self.assertNotIn("python3 -m compileall", installer + common)
        self.assertNotIn("config generated sh systemd tests", installer)
        self.assertNotIn("tests/test_common.py", common)
        self.assertIn("remove_development_files", common)

    def test_non_terminal_output_does_not_contain_color_codes(self) -> None:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; log_step check; log_success done',
                "test",
                str(COMMON),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={"TERM": "dumb"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("==> check", result.stdout)
        self.assertIn("✓ done", result.stdout)

    def test_bootstrap_does_not_depend_on_git(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("wget", content)
        self.assertIn("curl", content)
        self.assertNotIn("git clone", content)

    def test_query_log_switch_defaults_off_and_writes_to_stdout(self) -> None:
        environment = (ROOT / "config/dnsdist-automation").read_text(encoding="utf-8")
        dnsdist_config = (ROOT / "config/dnsdist.conf").read_text(encoding="utf-8")
        self.assertIn("DNSDIST_QUERY_LOG=0", environment)
        self.assertIn('os.getenv("DNSDIST_QUERY_LOG")', dnsdist_config)
        self.assertIn('LogAction("", false, true, false, false)', dnsdist_config)
        self.assertLess(
            dnsdist_config.index('name = "query-log"'),
            dnsdist_config.index("local domainRules = dofile"),
        )

    def test_systemd_units_can_be_staged_for_a_final_install_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            staged_root = Path(temporary_directory) / "staged"
            systemd_dir = staged_root / "systemd"
            systemd_dir.mkdir(parents=True)
            unit = systemd_dir / "dnsdist-test.service"
            unit.write_text(
                "EnvironmentFile=-@INSTALL_DIR@/config/dnsdist-automation\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; render_systemd_units "$2" "$3"',
                    "test",
                    str(COMMON),
                    str(staged_root),
                    "/opt/mydnsdist",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = unit.read_text(encoding="utf-8")
            self.assertIn(
                "EnvironmentFile=-/opt/mydnsdist/config/dnsdist-automation",
                rendered,
            )
            self.assertNotIn(str(staged_root), rendered)
            self.assertNotIn("@INSTALL_DIR@", rendered)

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
            self.assertEqual(target.resolve(), source.resolve())

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
                    'source "$1"; dns_listener_conflicts "$2" "$3" "$4" "$5"',
                    "test",
                    str(COMMON),
                    "10.133.0.1",
                    "5353",
                    local_address,
                    process_info,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        self.assertTrue(conflicts("*:5353", 'users:(("AdGuardHome",pid=1,fd=2))'))
        self.assertTrue(conflicts("10.133.0.1:5353", ""))
        self.assertFalse(conflicts("*:53", 'users:(("AdGuardHome",pid=1,fd=2))'))
        self.assertFalse(conflicts("10.133.0.2:5353", ""))
        self.assertFalse(
            conflicts("10.133.0.1:5353", 'users:(("dnsdist",pid=1,fd=2))')
        )

    def test_mihomo_udp_listener_check_accepts_wildcard_socket(self) -> None:
        result = subprocess.run(
            [
                "bash",
                "-c",
                (
                    'source "$1"; '
                    "ss() { printf '%s\\n' "
                    "'UNCONN 0 0 *:253 *:* users:((\"mihomo\",pid=1,fd=2))'; }; "
                    'check_mihomo_listener "127.0.0.1:253"'
                ),
                "test",
                str(COMMON),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
