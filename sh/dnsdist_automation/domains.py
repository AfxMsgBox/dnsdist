from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .common import activate_generated_file, exclusive_lock


DEFAULT_PROXY_URLS = (
    "https://raw.githubusercontent.com/AfxMsgBox/MyRule/main/domain/myproxylist.txt",
    "https://raw.githubusercontent.com/AfxMsgBox/MyRule/main/domain/gpt.txt",
    "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/gfw.txt",
    "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/tld-not-cn.txt",
)
DEFAULT_AD_URL = "https://adguardteam.github.io/HostlistsRegistry/assets/filter_29.txt"
DEFAULT_INSTALL_DIR = Path(os.getenv("DNSDIST_INSTALL_DIR", "/opt/mydnsdist"))
DEFAULT_TARGET = DEFAULT_INSTALL_DIR / "generated/domain-rules.lua"
DEFAULT_MAIN_CONFIG = DEFAULT_INSTALL_DIR / "config/dnsdist.conf"
DEFAULT_LOCK = DEFAULT_INSTALL_DIR / "generated/.domain-update.lock"
USER_AGENT = "dnsdist-rule-updater/2.0"

LABEL_RE = re.compile(r"^[a-z0-9_-]{1,63}$", re.IGNORECASE)
HOSTS_ADDRESSES = {"0", "0.0.0.0", "127.0.0.1", "::", "::1"}
SUPPORTED_QTYPES = {
    "A",
    "AAAA",
    "CAA",
    "CNAME",
    "HTTPS",
    "MX",
    "NAPTR",
    "NS",
    "PTR",
    "SOA",
    "SRV",
    "SVCB",
    "TXT",
}


@dataclass
class RuleBucket:
    exact: set[str] = field(default_factory=set)
    suffixes: set[str] = field(default_factory=set)
    regexes: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.exact or self.suffixes or self.regexes)


@dataclass
class ParseStats:
    total_lines: int = 0
    candidate_rules: int = 0
    exact: int = 0
    suffix: int = 0
    regex: int = 0
    optimized_regex: int = 0
    exceptions: int = 0
    typed: int = 0
    important: int = 0
    badfilter: int = 0
    unsupported: int = 0
    invalid: int = 0
    ignored: int = 0


@dataclass
class AdRules:
    block: RuleBucket = field(default_factory=RuleBucket)
    important: RuleBucket = field(default_factory=RuleBucket)
    allow: RuleBucket = field(default_factory=RuleBucket)
    typed_block: dict[tuple[str, ...], RuleBucket] = field(default_factory=dict)
    typed_important: dict[tuple[str, ...], RuleBucket] = field(default_factory=dict)
    typed_allow: dict[tuple[str, ...], RuleBucket] = field(default_factory=dict)
    stats: ParseStats = field(default_factory=ParseStats)


@dataclass
class ProxyRules:
    rules: RuleBucket = field(default_factory=RuleBucket)
    stats: ParseStats = field(default_factory=ParseStats)


@dataclass(frozen=True)
class BuildResult:
    content: str
    report: dict[str, object]


@dataclass(frozen=True)
class _RawAdRule:
    pattern: str
    exception: bool
    modifiers: tuple[str, ...]

    @property
    def disable_key(self) -> tuple[bool, str, tuple[str, ...]]:
        modifiers = tuple(item for item in self.modifiers if item.lower() != "badfilter")
        return self.exception, self.pattern, tuple(sorted(modifiers))


def fetch_text(
    url: str,
    *,
    timeout: float = 30,
    max_bytes: int = 50 * 1024 * 1024,
) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        declared_length = response.headers.get("Content-Length")
        if declared_length and int(declared_length) > max_bytes:
            raise ValueError(f"download is too large: {url}")
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"download exceeded {max_bytes} bytes: {url}")
    return payload.decode("utf-8-sig")


def normalize_domain(value: str) -> str | None:
    candidate = value.strip().strip(".").lower()
    if not candidate or len(candidate) > 253:
        return None
    try:
        ipaddress.ip_address(candidate)
        return None
    except ValueError:
        pass

    encoded_labels: list[str] = []
    for label in candidate.split("."):
        if not label:
            return None
        try:
            encoded = label.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        if not LABEL_RE.fullmatch(encoded):
            return None
        encoded_labels.append(encoded)
    normalized = ".".join(encoded_labels)
    return normalized if len(normalized) <= 253 else None


def _strip_yaml_scalar(value: str) -> str:
    scalar = value.strip().rstrip(",").strip()
    if len(scalar) >= 2 and scalar[0] == scalar[-1] and scalar[0] in {"'", '"'}:
        scalar = scalar[1:-1]
    return scalar.strip()


def _posix_literal(value: str) -> str:
    return "".join(
        f"\\{character}" if character in r"\.^$|?*+()[]{}" else character
        for character in value
    )


def parse_proxy_source(text: str) -> ProxyRules:
    result = ProxyRules()
    for raw_line in text.splitlines():
        result.stats.total_lines += 1
        line = raw_line.strip()
        if not line or line.startswith("#") or line in {"payload:", "payload"}:
            result.stats.ignored += 1
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        line = _strip_yaml_scalar(line)
        result.stats.candidate_rules += 1

        upper = line.upper()
        if upper.startswith("DOMAIN-SUFFIX,"):
            value = line.split(",", 2)[1]
            domain = normalize_domain(value)
            if domain:
                result.rules.suffixes.add(domain)
                result.stats.suffix += 1
            else:
                result.stats.invalid += 1
            continue
        elif upper.startswith("DOMAIN,"):
            value = line.split(",", 2)[1]
            domain = normalize_domain(value)
            if domain:
                result.rules.exact.add(domain)
                result.stats.exact += 1
            else:
                result.stats.invalid += 1
            continue
        elif upper.startswith("DOMAIN-REGEX,"):
            expression = line.split(",", 2)[1].strip()
            lowered = lower_safe_regex(expression)
            if lowered:
                kind, value = lowered
                getattr(
                    result.rules, "exact" if kind == "exact" else "suffixes"
                ).add(value)
                result.stats.optimized_regex += 1
                setattr(result.stats, kind, getattr(result.stats, kind) + 1)
            elif expression:
                result.rules.regexes.add(expression)
                result.stats.regex += 1
            else:
                result.stats.invalid += 1
            continue
        elif "," in line:
            result.stats.unsupported += 1
            continue

        if line.startswith("+."):
            line = line[2:]
            target = result.rules.suffixes
            kind = "suffix"
        elif line.startswith("."):
            line = line[1:]
            target = result.rules.suffixes
            kind = "suffix"
        elif line.startswith("*."):
            domain = normalize_domain(line[2:])
            if domain:
                result.rules.regexes.add(r"^.+\." + _posix_literal(domain) + "$")
                result.stats.regex += 1
            else:
                result.stats.invalid += 1
            continue
        elif line.startswith("/") and line.endswith("/"):
            expression = line[1:-1].strip()
            lowered = lower_safe_regex(expression)
            if lowered:
                kind, value = lowered
                getattr(
                    result.rules, "exact" if kind == "exact" else "suffixes"
                ).add(value)
                result.stats.optimized_regex += 1
                setattr(result.stats, kind, getattr(result.stats, kind) + 1)
            elif expression:
                result.rules.regexes.add(expression)
                result.stats.regex += 1
            else:
                result.stats.invalid += 1
            continue
        else:
            target = result.rules.exact
            kind = "exact"

        if any(character in line for character in "*?/|^"):
            result.stats.unsupported += 1
            continue
        domain = normalize_domain(line)
        if domain:
            target.add(domain)
            setattr(result.stats, kind, getattr(result.stats, kind) + 1)
        else:
            result.stats.invalid += 1
    _optimize_bucket(result.rules)
    return result


def adblock_pattern_to_regex(pattern: str) -> str:
    value = pattern
    prefix = ""
    if value.startswith("||"):
        prefix = r"(^|\.)"
        value = value[2:]
    elif value.startswith("|"):
        prefix = "^"
        value = value[1:]

    force_end = value.endswith("^") or value.endswith("|")
    if force_end:
        value = value[:-1]

    pieces: list[str] = []
    for character in value:
        if character == "*":
            pieces.append(".*")
        elif character == "^":
            pieces.append(r"($|\.)")
        else:
            pieces.append(_posix_literal(character))
    return prefix + "".join(pieces) + ("$" if force_end else "")


def _split_ad_rule(line: str) -> _RawAdRule | None:
    exception = line.startswith("@@")
    if exception:
        line = line[2:]

    if line.startswith("/"):
        closing = line.rfind("/")
        if closing <= 0:
            return None
        pattern = line[: closing + 1]
        tail = line[closing + 1 :]
        if tail and not tail.startswith("$"):
            return None
        modifier_text = tail[1:] if tail else ""
    else:
        pattern, separator, modifier_text = line.partition("$")
        if not separator:
            modifier_text = ""

    pattern = pattern.strip()
    if not pattern:
        return None
    modifiers = tuple(item.strip() for item in modifier_text.split(",") if item.strip())
    return _RawAdRule(pattern, exception, modifiers)


def _literal_regex_domain(value: str) -> str | None:
    candidate = value.replace(r"\.", ".").replace(r"\-", "-")
    if "\\" in candidate or re.search(r"[\[\]{}()*+?|^$]", candidate):
        return None
    return normalize_domain(candidate)


def lower_safe_regex(expression: str) -> tuple[str, str] | None:
    """Lower only regex forms that are provably an exact or suffix match."""

    if expression.startswith("^") and expression.endswith("$"):
        domain = _literal_regex_domain(expression[1:-1])
        if domain:
            return "exact", domain

    for prefix in (r"^(.+\.)?", r"^(.*\.)?", r"^(?:.+\.)?", r"(^|\.)"):
        if expression.startswith(prefix) and expression.endswith("$"):
            domain = _literal_regex_domain(expression[len(prefix) : -1])
            if domain:
                return "suffix", domain
    return None


def regex_suffix_hint(expression: str) -> str | None:
    """Return a mandatory literal DNS suffix for a regex, when one is obvious."""

    if not expression.endswith("$"):
        return None
    body = expression[:-1]
    match = re.search(r"([A-Za-z0-9_-]+(?:\\\.[A-Za-z0-9_-]+)+)$", body)
    if not match:
        return None
    literal_tail = match.group(1)
    prefix = body[: match.start()]
    if prefix not in {"", "^"} and not prefix.endswith(r"\."):
        # The first literal label might only be the tail of a wildcard-matched
        # label (for example .*-ad.example.net), so start after its first dot.
        _, separator, literal_tail = literal_tail.partition(r"\.")
        if not separator:
            return None
    domain = _literal_regex_domain(literal_tail)
    return domain if domain and "." in domain else None


def _parse_modifiers(
    modifiers: Sequence[str],
) -> tuple[bool, tuple[str, ...] | None, bool] | None:
    important = False
    qtypes: tuple[str, ...] | None = None
    badfilter = False
    for modifier in modifiers:
        lowered = modifier.lower()
        if lowered == "important":
            important = True
        elif lowered == "badfilter":
            badfilter = True
        elif lowered.startswith("dnstype="):
            values = tuple(
                sorted({item.strip().upper() for item in modifier.split("=", 1)[1].split("|")})
            )
            if not values or any(value not in SUPPORTED_QTYPES for value in values):
                return None
            qtypes = values
        else:
            return None
    return important, qtypes, badfilter


def _bucket_for(
    rules: AdRules,
    *,
    exception: bool,
    important: bool,
    qtypes: tuple[str, ...] | None,
) -> RuleBucket:
    if exception:
        base = rules.allow
        typed = rules.typed_allow
    elif important:
        base = rules.important
        typed = rules.typed_important
    else:
        base = rules.block
        typed = rules.typed_block
    if qtypes is None:
        return base
    return typed.setdefault(qtypes, RuleBucket())


def _add_pattern(bucket: RuleBucket, pattern: str, stats: ParseStats) -> bool:
    if pattern.startswith("/") and pattern.endswith("/"):
        expression = pattern[1:-1].strip()
        if not expression:
            return False
        lowered = lower_safe_regex(expression)
        if lowered:
            kind, value = lowered
            getattr(bucket, "exact" if kind == "exact" else "suffixes").add(value)
            stats.optimized_regex += 1
            setattr(stats, kind, getattr(stats, kind) + 1)
        else:
            bucket.regexes.add(expression)
            stats.regex += 1
        return True

    if pattern.startswith("||") and not any(marker in pattern[2:] for marker in "*?|"):
        candidate = pattern[2:-1] if pattern.endswith("^") else pattern[2:]
        domain = normalize_domain(candidate)
        if domain:
            bucket.suffixes.add(domain)
            stats.suffix += 1
            return True

    if pattern.startswith("|") and pattern.endswith("|"):
        domain = normalize_domain(pattern[1:-1])
        if domain:
            bucket.exact.add(domain)
            stats.exact += 1
            return True

    domain = normalize_domain(pattern)
    if domain:
        bucket.exact.add(domain)
        stats.exact += 1
        return True

    if any(marker in pattern for marker in ("*", "^", "|")):
        expression = adblock_pattern_to_regex(pattern)
        lowered = lower_safe_regex(expression)
        if lowered:
            kind, value = lowered
            getattr(bucket, "exact" if kind == "exact" else "suffixes").add(value)
            stats.optimized_regex += 1
            setattr(stats, kind, getattr(stats, kind) + 1)
        elif expression:
            bucket.regexes.add(expression)
            stats.regex += 1
        return bool(expression)
    return False


def parse_ad_source(text: str) -> AdRules:
    rules = AdRules()
    raw_rules: list[_RawAdRule] = []
    for raw_line in text.splitlines():
        rules.stats.total_lines += 1
        line = raw_line.strip()
        if not line or line.startswith(("!", "#", "[")):
            rules.stats.ignored += 1
            continue

        fields = line.split()
        if fields and fields[0] in HOSTS_ADDRESSES:
            aliases: list[str] = []
            for item in fields[1:]:
                if item.startswith("#"):
                    break
                aliases.append(item)
            if not aliases:
                rules.stats.invalid += 1
                continue
            for alias in aliases:
                rules.stats.candidate_rules += 1
                domain = normalize_domain(alias)
                if domain:
                    rules.block.exact.add(domain)
                    rules.stats.exact += 1
                else:
                    rules.stats.invalid += 1
            continue
        if fields and _looks_like_ip(fields[0]):
            rules.stats.candidate_rules += 1
            rules.stats.unsupported += 1
            continue

        parsed = _split_ad_rule(line)
        if parsed is None:
            rules.stats.candidate_rules += 1
            rules.stats.invalid += 1
        else:
            raw_rules.append(parsed)

    disabled = {
        raw.disable_key
        for raw in raw_rules
        if any(modifier.lower() == "badfilter" for modifier in raw.modifiers)
    }
    for raw in raw_rules:
        rules.stats.candidate_rules += 1
        parsed_modifiers = _parse_modifiers(raw.modifiers)
        if parsed_modifiers is None:
            rules.stats.unsupported += 1
            continue
        important, qtypes, badfilter = parsed_modifiers
        if badfilter:
            rules.stats.badfilter += 1
            continue
        if raw.disable_key in disabled:
            rules.stats.badfilter += 1
            continue
        if raw.exception:
            rules.stats.exceptions += 1
        if important:
            rules.stats.important += 1
        if qtypes:
            rules.stats.typed += 1
        bucket = _bucket_for(
            rules,
            exception=raw.exception,
            important=important,
            qtypes=qtypes,
        )
        if not _add_pattern(bucket, raw.pattern, rules.stats):
            rules.stats.invalid += 1

    optimize_ad_rules(rules)
    return rules


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def minimize_suffixes(domains: Iterable[str]) -> list[str]:
    """Remove entries already covered by a shorter parent suffix."""

    kept: set[str] = set()
    ordered = sorted(set(domains), key=lambda item: (item.count("."), item))
    for domain in ordered:
        labels = domain.split(".")
        if any(".".join(labels[offset:]) in kept for offset in range(1, len(labels))):
            continue
        kept.add(domain)
    return sorted(kept)


def _covered_by_suffix(domain: str, suffixes: set[str]) -> bool:
    labels = domain.split(".")
    return any(".".join(labels[offset:]) in suffixes for offset in range(len(labels)))


def _optimize_bucket(bucket: RuleBucket) -> None:
    bucket.suffixes = set(minimize_suffixes(bucket.suffixes))
    bucket.exact = {
        domain for domain in bucket.exact if not _covered_by_suffix(domain, bucket.suffixes)
    }


def _remove_covered(target: RuleBucket, dominant: RuleBucket) -> None:
    target.exact -= dominant.exact
    target.exact = {
        domain for domain in target.exact if not _covered_by_suffix(domain, dominant.suffixes)
    }
    target.suffixes = {
        domain for domain in target.suffixes if not _covered_by_suffix(domain, dominant.suffixes)
    }
    target.regexes -= dominant.regexes


def _merge_bucket(target: RuleBucket, source: RuleBucket) -> None:
    target.exact.update(source.exact)
    target.suffixes.update(source.suffixes)
    target.regexes.update(source.regexes)


def optimize_ad_rules(rules: AdRules) -> None:
    buckets = [rules.block, rules.important, rules.allow]
    for mapping in (rules.typed_block, rules.typed_important, rules.typed_allow):
        buckets.extend(mapping.values())
    for bucket in buckets:
        _optimize_bucket(bucket)
    # An identical important blocking rule always runs before a normal rule.
    _remove_covered(rules.block, rules.important)


def _lua_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _lua_long_string(value: str) -> str:
    equals = ""
    while f"]{equals}]" in value:
        equals += "="
    return f"[{equals}[{value}]{equals}]"


def _qtype_selector(qtypes: Sequence[str]) -> str:
    rules = [f"QTypeRule(DNSQType.{qtype})" for qtype in qtypes]
    return rules[0] if len(rules) == 1 else f"OrRule({{{', '.join(rules)}}})"


def _append_selector(
    lines: list[str], category: str, selector: str, qtypes: Sequence[str] | None
) -> None:
    if qtypes:
        selector = f"AndRule({{{selector}, {_qtype_selector(qtypes)}}})"
    lines.append(f"table.insert(result.{category}, {selector})")


def _render_bucket(
    lines: list[str],
    category: str,
    bucket: RuleBucket,
    stem: str,
    qtypes: Sequence[str] | None = None,
) -> None:
    if bucket.exact:
        name = f"{stem}Exact"
        lines.extend([f"local {name} = newDNSNameSet()", "for _, value in ipairs({"])
        lines.extend(f"  {_lua_string(domain)}," for domain in sorted(bucket.exact))
        lines.extend([f"}}) do {name}:add(newDNSName(value)) end"])
        _append_selector(lines, category, f"QNameSetRule({name})", qtypes)
        lines.append("")

    if bucket.suffixes:
        name = f"{stem}Suffix"
        lines.append(f"local {name} = newSuffixMatchNode()")
        domains = sorted(bucket.suffixes)
        for offset in range(0, len(domains), 5000):
            lines.append(f"{name}:add({{")
            lines.extend(
                f"  {_lua_string(domain)}," for domain in domains[offset : offset + 5000]
            )
            lines.append("})")
        _append_selector(lines, category, f"makeSuffixRule({name}, true)", qtypes)
        lines.append("")

    hinted: list[tuple[str, str]] = []
    global_regexes: list[str] = []
    for expression in sorted(bucket.regexes):
        hint = regex_suffix_hint(expression)
        if hint:
            hinted.append((expression, hint))
        else:
            global_regexes.append(expression)

    if hinted:
        hint_name = f"{stem}RegexSuffix"
        lines.append(f"local {hint_name} = newSuffixMatchNode()")
        lines.append(f"{hint_name}:add({{")
        lines.extend(f"  {_lua_string(value)}," for value in minimize_suffixes(h for _, h in hinted))
        lines.append("})")
        regex_selector = _regex_selector(expression for expression, _ in hinted)
        selector = f"AndRule({{makeSuffixRule({hint_name}, true), {regex_selector}}})"
        _append_selector(lines, category, selector, qtypes)
        lines.append("")

    if global_regexes:
        _append_selector(lines, category, _regex_selector(global_regexes), qtypes)
        lines.append("")


def _regex_selector(expressions: Iterable[str]) -> str:
    selectors = [f"RegexRule({_lua_long_string(value)})" for value in expressions]
    return selectors[0] if len(selectors) == 1 else f"OrRule({{{', '.join(selectors)}}})"


def render_domain_rules(ad_rules: AdRules, proxy_rules: RuleBucket) -> str:
    _optimize_bucket(proxy_rules)
    stats = ad_rules.stats
    lines = [
        "-- AUTO-GENERATED by update-dnsdist-domains.py. DO NOT EDIT.",
        (
            f"-- parsed ad rules: exact={stats.exact}, suffix={stats.suffix}, "
            f"regex={stats.regex}, lowered_regex={stats.optimized_regex}, "
            f"unsupported={stats.unsupported}; proxy_exact={len(proxy_rules.exact)}, "
            f"proxy_suffix={len(proxy_rules.suffixes)}, proxy_regex={len(proxy_rules.regexes)}"
        ),
        "",
        "local result = { important = {}, allow = {}, block = {}, proxy = {} }",
        "local makeSuffixRule = QNameSuffixRule or SuffixMatchNodeRule",
        "if makeSuffixRule == nil then",
        '  error("dnsdist does not provide a suffix-match rule selector")',
        "end",
        "",
    ]
    _render_bucket(lines, "important", ad_rules.important, "adImportant")
    _render_bucket(lines, "allow", ad_rules.allow, "adAllow")
    _render_bucket(lines, "block", ad_rules.block, "adBlock")
    for index, (qtypes, bucket) in enumerate(sorted(ad_rules.typed_important.items())):
        _render_bucket(lines, "important", bucket, f"adTypedImportant{index}", qtypes)
    for index, (qtypes, bucket) in enumerate(sorted(ad_rules.typed_allow.items())):
        _render_bucket(lines, "allow", bucket, f"adTypedAllow{index}", qtypes)
    for index, (qtypes, bucket) in enumerate(sorted(ad_rules.typed_block.items())):
        _render_bucket(lines, "block", bucket, f"adTypedBlock{index}", qtypes)

    _render_bucket(lines, "proxy", proxy_rules, "proxy")
    lines.extend(["return result", ""])
    return "\n".join(lines)


def configured_proxy_urls() -> tuple[str, ...]:
    raw = os.getenv("DNSDIST_PROXY_URLS")
    if raw is None:
        return DEFAULT_PROXY_URLS
    urls = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not urls:
        raise ValueError("DNSDIST_PROXY_URLS must contain at least one URL")
    return urls


def _bucket_counts(bucket: RuleBucket) -> dict[str, int]:
    return {
        "exact": len(bucket.exact),
        "suffix": len(bucket.suffixes),
        "regex": len(bucket.regexes),
    }


def _ad_output_counts(rules: AdRules) -> dict[str, object]:
    return {
        "block": _bucket_counts(rules.block),
        "important": _bucket_counts(rules.important),
        "allow": _bucket_counts(rules.allow),
        "typed_groups": {
            "block": len(rules.typed_block),
            "important": len(rules.typed_important),
            "allow": len(rules.typed_allow),
        },
    }


def _ad_regex_count(rules: AdRules) -> int:
    return sum(
        len(bucket.regexes)
        for bucket in (
            rules.block,
            rules.important,
            rules.allow,
            *rules.typed_block.values(),
            *rules.typed_important.values(),
            *rules.typed_allow.values(),
        )
    )


def _validate_drift(rules: AdRules, *, max_regex: int, max_unsupported_ratio: float) -> None:
    regex_count = _ad_regex_count(rules)
    if regex_count > max_regex:
        raise RuntimeError(f"advertisement regex count {regex_count} exceeds {max_regex}")
    denominator = max(rules.stats.candidate_rules, 1)
    ratio = rules.stats.unsupported / denominator
    if ratio > max_unsupported_ratio:
        raise RuntimeError(
            f"unsupported advertisement rule ratio {ratio:.2%} exceeds "
            f"{max_unsupported_ratio:.2%}"
        )


def build_rules(
    proxy_urls: Sequence[str],
    ad_url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_regex: int = 2048,
    max_unsupported_ratio: float = 0.05,
    enforce_limits: bool = True,
) -> BuildResult:
    proxy_rules = RuleBucket()
    proxy_source_counts: dict[str, object] = {}
    for url in proxy_urls:
        parsed = parse_proxy_source(fetch_text(url, timeout=timeout, max_bytes=max_bytes))
        if parsed.rules.is_empty():
            raise RuntimeError(f"proxy-domain source produced an empty rule set: {url}")
        proxy_source_counts[url] = {
            "parse": asdict(parsed.stats),
            "output": _bucket_counts(parsed.rules),
        }
        _merge_bucket(proxy_rules, parsed.rules)
    ad_rules = parse_ad_source(fetch_text(ad_url, timeout=timeout, max_bytes=max_bytes))
    if proxy_rules.is_empty():
        raise RuntimeError("all proxy-domain sources produced an empty rule set")
    if all(
        bucket.is_empty()
        for bucket in (
            ad_rules.block,
            ad_rules.important,
            ad_rules.allow,
            *ad_rules.typed_block.values(),
            *ad_rules.typed_important.values(),
            *ad_rules.typed_allow.values(),
        )
    ):
        raise RuntimeError("the advertisement source produced an empty rule set")
    if enforce_limits:
        _validate_drift(
            ad_rules,
            max_regex=max_regex,
            max_unsupported_ratio=max_unsupported_ratio,
        )
    proxy_input_counts = _bucket_counts(proxy_rules)
    _optimize_bucket(proxy_rules)
    report: dict[str, object] = {
        "ad_source": ad_url,
        "ad_parse": asdict(ad_rules.stats),
        "ad_output": _ad_output_counts(ad_rules),
        "proxy_sources": proxy_source_counts,
        "proxy_input": proxy_input_counts,
        "proxy_output": _bucket_counts(proxy_rules),
        "limits": {
            "max_ad_regex_rules": max_regex,
            "max_unsupported_ad_ratio": max_unsupported_ratio,
            "ad_regex_rules": _ad_regex_count(ad_rules),
            "unsupported_ad_ratio": (
                ad_rules.stats.unsupported / max(ad_rules.stats.candidate_rules, 1)
            ),
            "enforced": enforce_limits,
        },
    }
    return BuildResult(render_domain_rules(ad_rules, proxy_rules), report)


def _count_text(counts: dict[str, object]) -> str:
    return (
        f"精确 {counts['exact']}，后缀 {counts['suffix']}，"
        f"正则 {counts['regex']}"
    )


def format_report(report: dict[str, object], result_text: str) -> str:
    ad_parse = report["ad_parse"]
    ad_output = report["ad_output"]
    proxy_input = report["proxy_input"]
    proxy_output = report["proxy_output"]
    proxy_sources = report["proxy_sources"]
    limits = report["limits"]
    assert isinstance(ad_parse, dict)
    assert isinstance(ad_output, dict)
    assert isinstance(proxy_input, dict)
    assert isinstance(proxy_output, dict)
    assert isinstance(proxy_sources, dict)
    assert isinstance(limits, dict)

    lines = [
        "域名规则统计",
        "",
        "广告规则：",
        f"  来源：{report['ad_source']}",
        (
            f"  解析：总行数 {ad_parse['total_lines']}，候选规则 "
            f"{ad_parse['candidate_rules']}，忽略 {ad_parse['ignored']}，"
            f"无效 {ad_parse['invalid']}，不支持 {ad_parse['unsupported']}"
        ),
        f"  最终阻断：{_count_text(ad_output['block'])}",
        f"  最终放行：{_count_text(ad_output['allow'])}",
        f"  最终高优先级阻断：{_count_text(ad_output['important'])}",
        (
            "  查询类型规则组：阻断 "
            f"{ad_output['typed_groups']['block']}，放行 "
            f"{ad_output['typed_groups']['allow']}，高优先级 "
            f"{ad_output['typed_groups']['important']}"
        ),
        "",
        "代理规则：",
        f"  各来源合并后：{_count_text(proxy_input)}",
        f"  优化后最终代理匹配规则：{_count_text(proxy_output)}",
        "  各来源：",
    ]
    for url, source in proxy_sources.items():
        assert isinstance(source, dict)
        lines.append(f"    {url}：{_count_text(source['output'])}")

    enforced = bool(limits["enforced"])
    status = "已启用" if enforced else "仅统计，未强制"
    lines.extend(
        [
            "",
            "安全检查：",
            (
                f"  广告正则 {limits['ad_regex_rules']} / "
                f"{limits['max_ad_regex_rules']}；不支持规则占比 "
                f"{limits['unsupported_ad_ratio']:.2%} / "
                f"{limits['max_unsupported_ad_ratio']:.2%}（{status}）"
            ),
            "",
            f"结果：{result_text}",
        ]
    )
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate optimized dnsdist advertisement and proxy-domain rules"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("DNSDIST_DOMAIN_RULES", str(DEFAULT_TARGET))),
    )
    parser.add_argument(
        "--main-config",
        type=Path,
        default=Path(os.getenv("DNSDIST_MAIN_CONFIG", str(DEFAULT_MAIN_CONFIG))),
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(os.getenv("DNSDIST_DOMAIN_LOCK", str(DEFAULT_LOCK))),
    )
    parser.add_argument("--no-check", action="store_true")
    parser.add_argument("--no-reload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="download and analyze rules without writing the generated file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    timeout = float(os.getenv("DNSDIST_FETCH_TIMEOUT", "30"))
    max_bytes = int(os.getenv("DNSDIST_MAX_DOWNLOAD_BYTES", str(50 * 1024 * 1024)))
    max_regex = int(os.getenv("DNSDIST_MAX_AD_REGEX_RULES", "2048"))
    max_unsupported_ratio = float(
        os.getenv("DNSDIST_MAX_UNSUPPORTED_AD_RATIO", "0.05")
    )
    ad_url = os.getenv("DNSDIST_AD_URL", DEFAULT_AD_URL)

    try:
        result = build_rules(
            configured_proxy_urls(),
            ad_url,
            timeout=timeout,
            max_bytes=max_bytes,
            max_regex=max_regex,
            max_unsupported_ratio=max_unsupported_ratio,
            enforce_limits=not args.stats_only,
        )
        if args.dry_run:
            sys.stdout.write(result.content)
            return 0
        if args.stats_only:
            print(format_report(result.report, "仅统计，未写入规则"))
            return 0
        with exclusive_lock(args.lock_file):
            changed = activate_generated_file(
                args.output,
                result.content,
                args.main_config,
                check=not args.no_check,
                reload_service=not args.no_reload,
            )
    except Exception as exc:
        print(f"update-dnsdist-domains: {exc}", file=sys.stderr)
        return 1

    result_text = "域名规则已更新" if changed else "域名规则无变化"
    print(format_report(result.report, result_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
