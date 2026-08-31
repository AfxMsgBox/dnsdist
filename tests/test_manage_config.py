from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sh"))

import importlib.util  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "manage_config", ROOT / "sh" / "manage-config.py"
)
assert SPEC and SPEC.loader
manage_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage_config)


class ManageConfigTest(unittest.TestCase):
    def test_merge_keeps_local_values_and_adds_new_defaults(self) -> None:
        template = (
            "# values\n"
            "DNSDIST_INSTALL_DIR=/opt/mydnsdist\n"
            "DNSDIST_WG_DNS_IP=10.0.0.1\n"
            "DNSDIST_WG_DNS_PORT=53\n"
            "DNSDIST_WG_NETWORK=10.0.0.0/24\n"
            "DNSDIST_QUERY_LOG=0\n"
            "A=one\nB=two\n"
        )
        rendered = manage_config.render_config(
            template,
            {"A": "local value", "OLD": "kept"},
            install_dir=Path("/srv/dnsdist"),
            interactive=False,
        )
        self.assertIn("DNSDIST_INSTALL_DIR=/srv/dnsdist", rendered)
        self.assertIn("DNSDIST_WG_DNS_PORT=53", rendered)
        self.assertIn("DNSDIST_QUERY_LOG=0", rendered)
        self.assertIn("A='local value'", rendered)
        self.assertIn("B=two", rendered)
        self.assertIn("OLD=kept", rendered)

    def test_read_values_supports_shell_quoting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("A='value with spaces'\nB=https://example.com/x\n")
            self.assertEqual(
                manage_config.read_values(config),
                {"A": "value with spaces", "B": "https://example.com/x"},
            )

    def test_validation_rejects_invalid_endpoint_and_ratio(self) -> None:
        with self.assertRaises(ValueError):
            manage_config.validate_endpoint("127.0.0.1")
        with self.assertRaises(ValueError):
            manage_config.validate_ratio("1.1")

    def test_dns_port_override_and_validation(self) -> None:
        template = (
            "DNSDIST_INSTALL_DIR=/opt/mydnsdist\n"
            "DNSDIST_WG_DNS_IP=10.0.0.1\n"
            "DNSDIST_WG_DNS_PORT=53\n"
            "DNSDIST_WG_NETWORK=10.0.0.0/24\n"
        )
        rendered = manage_config.render_config(
            template,
            {},
            install_dir=Path("/opt/mydnsdist"),
            interactive=False,
            fixed_values={"DNSDIST_WG_DNS_PORT": "5353"},
        )
        self.assertIn("DNSDIST_WG_DNS_PORT=5353", rendered)
        with self.assertRaises(ValueError):
            manage_config.validate_integer("0", 1, 65535)
        with self.assertRaises(ValueError):
            manage_config.validate_integer("65536", 1, 65535)

    def test_parse_wireguard_runtime_address(self) -> None:
        self.assertEqual(
            manage_config.parse_wireguard_ipv4(
                "7: wg-pub    inet 10.133.0.1/24 scope global wg-pub"
            ),
            ("10.133.0.1", "10.133.0.0/24"),
        )

    def test_all_wireguard_interfaces_are_listed_with_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            (config_dir / "wg-config.conf").write_text(
                "[Interface]\nAddress = 10.20.0.1/24\n",
                encoding="utf-8",
            )

            def run_command(arguments: tuple[str, ...]) -> str:
                if arguments == ("wg", "show", "interfaces"):
                    return "wg0 wg-pub\n"
                if arguments[-1] == "wg0":
                    return "7: wg0 inet 10.10.0.1/24 scope global wg0\n"
                if arguments[-1] == "wg-pub":
                    return "8: wg-pub inet 10.133.0.1/24 scope global wg-pub\n"
                return ""

            with patch.object(manage_config, "_run_command", side_effect=run_command):
                interfaces = manage_config.inspect_wireguard_interfaces(
                    config_dir=config_dir
                )

        self.assertEqual(
            [item.name for item in interfaces],
            ["wg0", "wg-pub", "wg-config"],
        )
        output = StringIO()
        with redirect_stdout(output):
            manage_config.print_detected_values({}, interfaces)
        rendered = output.getvalue()
        self.assertIn("wg0：运行中，运行地址 10.10.0.1/24", rendered)
        self.assertIn("wg-pub：运行中，运行地址 10.133.0.1/24", rendered)
        self.assertIn("wg-config：未运行，配置地址 10.20.0.1/24", rendered)

    def test_mihomo_runtime_arguments_and_dns_config(self) -> None:
        self.assertEqual(
            manage_config.mihomo_config_candidates(
                ("/etc/proxy/core/mihomo", "-d", "/etc/proxy/core")
            ),
            [
                Path("/etc/proxy/core/config.yaml"),
                Path("/etc/proxy/core/config.yml"),
            ],
        )
        self.assertEqual(
            manage_config.parse_mihomo_dns(
                "dns:\n  enable: true\n  listen: :253\nproxies: []\n"
            ),
            "127.0.0.1:253",
        )
        self.assertIsNone(
            manage_config.parse_mihomo_dns(
                "dns:\n  enable: false\n  listen: 0.0.0.0:253\n"
            )
        )

    def test_mihomo_download_proxy_prefers_mixed_then_http_port(self) -> None:
        self.assertEqual(
            manage_config.parse_mihomo_download_proxy(
                "port: 7891\nmixed-port: 7890\nsocks-port: 7892\n"
            ),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            manage_config.parse_mihomo_download_proxy(
                "dns:\n  listen: :253\nport: 8080\n"
            ),
            "http://127.0.0.1:8080",
        )
        self.assertIsNone(
            manage_config.parse_mihomo_download_proxy("socks-port: 7892\n")
        )

    def test_explicit_mihomo_config_argument_takes_priority(self) -> None:
        self.assertEqual(
            manage_config.mihomo_config_candidates(
                ("mihomo", "-d", "/etc/mihomo", "--config=/run/mihomo.yaml")
            ),
            [Path("/run/mihomo.yaml")],
        )

    def test_old_ecs_defaults_are_migrated_to_full_addresses(self) -> None:
        values = {
            "DNSDIST_ECS_PREFIX_V4": "24",
            "DNSDIST_ECS_PREFIX_V6": "56",
        }
        with redirect_stdout(StringIO()):
            manage_config.migrate_old_defaults(values)
        self.assertEqual(values["DNSDIST_ECS_PREFIX_V4"], "32")
        self.assertEqual(values["DNSDIST_ECS_PREFIX_V6"], "128")

        custom = {
            "DNSDIST_ECS_PREFIX_V4": "20",
            "DNSDIST_ECS_PREFIX_V6": "64",
        }
        manage_config.migrate_old_defaults(custom)
        self.assertEqual(custom["DNSDIST_ECS_PREFIX_V4"], "20")
        self.assertEqual(custom["DNSDIST_ECS_PREFIX_V6"], "64")

    def test_advanced_parameters_are_not_interactive(self) -> None:
        for name in (
            "DNSDIST_ECS_PREFIX_V4",
            "DNSDIST_ECS_PREFIX_V6",
            "DNSDIST_QUERY_LOG",
            "DNSDIST_MAX_DOWNLOAD_BYTES",
            "DNSDIST_MAX_AD_REGEX_RULES",
            "DNSDIST_MAX_UNSUPPORTED_AD_RATIO",
            "DNSDIST_DOWNLOAD_PROXY",
        ):
            self.assertNotIn(name, manage_config.INTERACTIVE_NAMES)

    def test_query_log_switch_accepts_only_zero_or_one(self) -> None:
        manage_config.VALIDATORS["DNSDIST_QUERY_LOG"]("0")
        manage_config.VALIDATORS["DNSDIST_QUERY_LOG"]("1")
        with self.assertRaises(ValueError):
            manage_config.VALIDATORS["DNSDIST_QUERY_LOG"]("yes")

    def test_download_proxy_may_be_http_https_or_empty(self) -> None:
        validator = manage_config.VALIDATORS["DNSDIST_DOWNLOAD_PROXY"]
        validator("")
        validator("http://127.0.0.1:7890")
        validator("https://proxy.example:8443")
        with self.assertRaises(ValueError):
            validator("socks5://127.0.0.1:7891")

    def test_detected_proxy_requires_interactive_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template"
            output = root / "output"
            template.write_text(
                "DNSDIST_INSTALL_DIR=/opt/mydnsdist\n"
                "DNSDIST_WG_DNS_IP=10.0.0.1\n"
                "DNSDIST_WG_NETWORK=10.0.0.0/24\n"
                "DNSDIST_DOWNLOAD_PROXY=\n",
                encoding="utf-8",
            )
            detected = {
                "DNSDIST_WG_DNS_IP": "10.0.0.1",
                "DNSDIST_WG_NETWORK": "10.0.0.0/24",
                "DNSDIST_DOWNLOAD_PROXY": "http://127.0.0.1:7890",
            }
            with (
                patch.object(manage_config, "inspect_wireguard_interfaces", return_value=[]),
                patch.object(manage_config, "detect_system_values", return_value=detected),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(
                    manage_config.main(
                        [
                            "--template",
                            str(template),
                            "--output",
                            str(output),
                            "--install-dir",
                            "/opt/mydnsdist",
                            "--detect-system",
                        ]
                    ),
                    0,
                )
            self.assertIn(
                "DNSDIST_DOWNLOAD_PROXY=''",
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
