from __future__ import annotations

import ipaddress
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sh"))

from dnsdist_automation.ecs import (  # noqa: E402
    endpoint_to_ecs,
    parse_endpoint_ip,
    parse_wg_dump,
    render_ecs_rules,
)


class EcsRulesTest(unittest.TestCase):
    def test_endpoint_parsing(self) -> None:
        self.assertEqual(str(parse_endpoint_ip("8.8.8.8:51820")), "8.8.8.8")
        self.assertEqual(
            str(parse_endpoint_ip("[2001:4860:4860::8888]:51820")),
            "2001:4860:4860::8888",
        )
        self.assertIsNone(parse_endpoint_ip("(none)"))
        self.assertIsNone(parse_endpoint_ip("hostname.example:51820"))

    def test_endpoint_prefix(self) -> None:
        self.assertEqual(
            str(endpoint_to_ecs(ipaddress.ip_address("8.8.8.9"))),
            "8.8.8.9/32",
        )
        self.assertEqual(
            str(endpoint_to_ecs(ipaddress.ip_address("2001:4860:4860::8888"))),
            "2001:4860:4860::8888/128",
        )

    def test_wg_dump_filters_sources_and_non_global_endpoints(self) -> None:
        dump = (ROOT / "tests/fixtures/wg-dump.txt").read_text(encoding="utf-8")
        mappings = parse_wg_dump(dump, ipaddress.ip_network("10.68.0.0/16"))
        self.assertEqual([str(item.source) for item in mappings], ["10.68.0.10/32", "10.68.0.11/32"])
        self.assertEqual(str(mappings[0].ecs), "8.8.8.8/32")
        self.assertEqual(str(mappings[1].ecs), "2001:4860:4860::8888/128")

        rendered = render_ecs_rules(mappings)
        self.assertIn("peerSourceNMG = newNMG()", rendered)
        self.assertIn('peerSourceNMG:addMask("10.68.0.10/32")', rendered)
        self.assertIn("NetmaskGroupRule(peerSourceNMG)", rendered)
        self.assertNotIn('NetmaskGroupRule("10.68.0.10/32")', rendered)
        self.assertIn('SetECSAction("8.8.8.8/32")', rendered)
        self.assertIn('PoolAction("china-ecs")', rendered)
        self.assertNotIn("10.68.0.12", rendered)

    def test_non_global_endpoint_can_be_opted_in(self) -> None:
        dump = (ROOT / "tests/fixtures/wg-dump.txt").read_text(encoding="utf-8")
        mappings = parse_wg_dump(
            dump,
            ipaddress.ip_network("10.68.0.0/16"),
            allow_non_global=True,
        )
        self.assertIn("10.68.0.12/32", {str(item.source) for item in mappings})


if __name__ == "__main__":
    unittest.main()
