from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sh"))

from dnsdist_automation.domains import (  # noqa: E402
    RuleBucket,
    _validate_drift,
    adblock_pattern_to_regex,
    build_rules,
    format_report,
    lower_safe_regex,
    minimize_suffixes,
    normalize_domain,
    parse_ad_source,
    parse_proxy_source,
    regex_suffix_hint,
    render_domain_rules,
)


class DomainRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        text = (ROOT / "tests/fixtures/ad-filter.txt").read_text(encoding="utf-8")
        self.rules = parse_ad_source(text)

    def test_normalize_domain(self) -> None:
        self.assertEqual(normalize_domain("Example.COM."), "example.com")
        self.assertEqual(normalize_domain("测试.example"), "xn--0zwm56d.example")
        self.assertIsNone(normalize_domain("192.0.2.1"))
        self.assertIsNone(normalize_domain("not a domain"))

    def test_proxy_yaml_parser(self) -> None:
        text = (ROOT / "tests/fixtures/proxy-list.txt").read_text(encoding="utf-8")
        parsed = parse_proxy_source(text)
        self.assertIn("example.com", parsed.rules.suffixes)
        self.assertNotIn("sub.example.com", parsed.rules.suffixes)
        self.assertIn("xn--0zwm56d.example", parsed.rules.suffixes)
        self.assertIn("exact.example.net", parsed.rules.exact)
        self.assertIn("only.example.org", parsed.rules.exact)
        self.assertIn("suffix.example.org", parsed.rules.suffixes)
        self.assertIn("lowered.example.org", parsed.rules.suffixes)
        self.assertIn(
            r"^special-[0-9]+\.regex.example.org$", parsed.rules.regexes
        )
        self.assertEqual(parsed.stats.invalid, 1)

    def test_adblock_exact_suffix_and_regex_are_separate(self) -> None:
        self.assertIn("plain.example.com", self.rules.block.exact)
        self.assertIn("telemetry.example.org", self.rules.block.exact)
        self.assertIn("telemetry-alias.example.org", self.rules.block.exact)
        self.assertIn("ads.example.com", self.rules.block.suffixes)
        self.assertNotIn("sub.ads.example.com", self.rules.block.suffixes)
        self.assertIn(r"^admaster\.", self.rules.block.regexes)

        wildcard = adblock_pattern_to_regex("||*-ad.example.net^")
        self.assertIn(wildcard, self.rules.block.regexes)
        self.assertEqual(regex_suffix_hint(wildcard), "example.net")

    def test_adblock_semantic_modifiers(self) -> None:
        self.assertIn("allowed.example", self.rules.allow.suffixes)
        self.assertIn("forced.example", self.rules.important.suffixes)
        self.assertIn(("A", "AAAA"), self.rules.typed_block)
        self.assertIn(
            "typed.example", self.rules.typed_block[("A", "AAAA")].suffixes
        )
        self.assertNotIn("disabled.example", self.rules.block.suffixes)
        self.assertEqual(self.rules.stats.unsupported, 1)
        self.assertGreaterEqual(self.rules.stats.badfilter, 1)

    def test_safe_regex_lowering_is_conservative(self) -> None:
        self.assertEqual(
            lower_safe_regex(r"^exact\.regex\.example$"),
            ("exact", "exact.regex.example"),
        )
        self.assertEqual(
            lower_safe_regex(r"^(.+\.)?suffix\.regex\.example$"),
            ("suffix", "suffix.regex.example"),
        )
        self.assertIsNone(lower_safe_regex(r"^admaster\."))
        self.assertIn("exact.regex.example", self.rules.block.exact)
        self.assertIn("suffix.regex.example", self.rules.block.suffixes)

    def test_suffix_minimization_and_render(self) -> None:
        minimized = minimize_suffixes(
            {"example.com", "sub.example.com", "other.example.net"}
        )
        self.assertEqual(minimized, ["example.com", "other.example.net"])
        rendered = render_domain_rules(
            self.rules,
            RuleBucket(suffixes={"example.com", "sub.example.com"}),
        )
        self.assertIn("newDNSNameSet()", rendered)
        self.assertIn("QNameSetRule", rendered)
        self.assertIn("newSuffixMatchNode()", rendered)
        self.assertIn("QNameSuffixRule(adBlockRegexSuffix, true)", rendered)
        self.assertIn("QTypeRule(DNSQType.A)", rendered)
        self.assertIn("return result", rendered)
        self.assertIn('"example.com"', rendered)
        self.assertNotIn('"sub.example.com"', rendered)

    def test_format_drift_guards(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "regex count"):
            _validate_drift(self.rules, max_regex=0, max_unsupported_ratio=1.0)
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            _validate_drift(self.rules, max_regex=100, max_unsupported_ratio=0.0)

        empty = parse_ad_source("||valid.example^\n")
        empty.block = RuleBucket(regexes={"one", "two"})
        with self.assertRaisesRegex(RuntimeError, "regex count"):
            _validate_drift(empty, max_regex=1, max_unsupported_ratio=1.0)

    def test_build_report_and_stats_mode(self) -> None:
        proxy = (ROOT / "tests/fixtures/proxy-list.txt").read_text(encoding="utf-8")
        ad = (ROOT / "tests/fixtures/ad-filter.txt").read_text(encoding="utf-8")

        def fake_fetch(url: str, **_: object) -> str:
            return proxy if url == "proxy-source" else ad

        with patch("dnsdist_automation.domains.fetch_text", side_effect=fake_fetch):
            result = build_rules(
                ("proxy-source",),
                "ad-source",
                timeout=1,
                max_bytes=1024,
                max_unsupported_ratio=0.0,
                enforce_limits=False,
            )
        self.assertIn("return result", result.content)
        self.assertEqual(result.report["ad_source"], "ad-source")
        self.assertFalse(result.report["limits"]["enforced"])
        self.assertGreater(result.report["proxy_output"]["suffix"], 0)
        report = format_report(result.report, "仅统计，未写入规则")
        self.assertIn("域名规则统计", report)
        self.assertIn("优化后最终代理匹配规则", report)
        self.assertIn("结果：仅统计，未写入规则", report)
        self.assertNotIn('{"', report)


if __name__ == "__main__":
    unittest.main()
