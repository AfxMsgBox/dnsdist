from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_explicit_mihomo_config_argument_takes_priority(self) -> None:
        self.assertEqual(
            manage_config.mihomo_config_candidates(
                ("mihomo", "-d", "/etc/mihomo", "--config=/run/mihomo.yaml")
            ),
            [Path("/run/mihomo.yaml")],
        )


if __name__ == "__main__":
    unittest.main()
