#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit


ASSIGNMENT_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9_=+.-]{1,15}$")

PROMPTS = {
    "DNSDIST_WG_DNS_IP": "dnsdist 监听的 WireGuard IPv4 地址",
    "DNSDIST_WG_DNS_PORT": "dnsdist 监听端口",
    "DNSDIST_WG_NETWORK": "允许查询的 WireGuard 网络",
    "DNSDIST_WG_INTERFACE": "WireGuard 接口名称",
    "DNSDIST_MIHOMO_ADDRESS": "Mihomo DNS 地址（主机:端口）",
    "DNSDIST_ALIDNS_1": "AliDNS 主服务器（主机:端口）",
    "DNSDIST_ALIDNS_2": "AliDNS 备用服务器（主机:端口）",
    "DNSDIST_ECS_PREFIX_V4": "IPv4 ECS 前缀长度",
    "DNSDIST_ECS_PREFIX_V6": "IPv6 ECS 前缀长度",
    "DNSDIST_ECS_ALLOW_NON_GLOBAL": "是否允许非公网 Endpoint（0/1）",
    "DNSDIST_PROXY_URLS": "代理域名规则 URL（逗号分隔）",
    "DNSDIST_AD_URL": "广告规则 URL",
    "DNSDIST_FETCH_TIMEOUT": "下载超时秒数",
    "DNSDIST_MAX_DOWNLOAD_BYTES": "单个规则文件最大字节数",
    "DNSDIST_MAX_AD_REGEX_RULES": "广告正则规则数量上限",
    "DNSDIST_MAX_UNSUPPORTED_AD_RATIO": "不支持广告规则占比上限",
}

INTERACTIVE_NAMES = {
    "DNSDIST_WG_DNS_IP",
    "DNSDIST_WG_DNS_PORT",
    "DNSDIST_WG_NETWORK",
    "DNSDIST_WG_INTERFACE",
    "DNSDIST_MIHOMO_ADDRESS",
    "DNSDIST_ALIDNS_1",
    "DNSDIST_ALIDNS_2",
}

MIGRATED_DEFAULTS = {
    "DNSDIST_ECS_PREFIX_V4": ("24", "32"),
    "DNSDIST_ECS_PREFIX_V6": ("56", "128"),
}


def parse_value(raw: str) -> str:
    if not raw:
        return ""
    values = shlex.split(raw, comments=False, posix=True)
    if len(values) != 1:
        raise ValueError(f"invalid environment value: {raw!r}")
    return values[0]


def read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = ASSIGNMENT_RE.match(line.strip())
        if not match:
            continue
        try:
            values[match.group(1)] = parse_value(match.group(2))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return values


def validate_ip(value: str) -> None:
    if ipaddress.ip_address(value).version != 4:
        raise ValueError("当前 dnsdist 监听配置要求使用 IPv4 地址")


def validate_network(value: str) -> None:
    ipaddress.ip_network(value, strict=False)


def validate_interface(value: str) -> None:
    if not INTERFACE_RE.fullmatch(value):
        raise ValueError("接口名称必须为 1 到 15 个有效字符")


def validate_endpoint(value: str) -> None:
    if value.startswith("["):
        closing = value.find("]")
        if closing < 1 or closing + 2 >= len(value) or value[closing + 1] != ":":
            raise ValueError("IPv6 地址必须使用 [地址]:端口 格式")
        host = value[1:closing]
        port = value[closing + 2 :]
        ipaddress.ip_address(host)
    else:
        if value.count(":") != 1:
            raise ValueError("地址必须使用 主机:端口 格式")
        host, port = value.rsplit(":", 1)
        if not host:
            raise ValueError("主机不能为空")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            raise ValueError("主机名包含无效字符")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("端口必须位于 1 到 65535")


def validate_integer(value: str, minimum: int, maximum: int) -> None:
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"数值必须位于 {minimum} 到 {maximum}")


def validate_positive_number(value: str) -> None:
    if float(value) <= 0:
        raise ValueError("数值必须大于 0")


def validate_ratio(value: str) -> None:
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError("比例必须位于 0 到 1")


def validate_bool(value: str) -> None:
    if value not in {"0", "1"}:
        raise ValueError("请输入 0 或 1")


def validate_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("必须是有效的 HTTP 或 HTTPS URL")


def validate_urls(value: str) -> None:
    urls = [item.strip() for item in value.split(",") if item.strip()]
    if not urls:
        raise ValueError("至少需要一个 URL")
    for url in urls:
        validate_url(url)


VALIDATORS: dict[str, Callable[[str], None]] = {
    "DNSDIST_WG_DNS_IP": validate_ip,
    "DNSDIST_WG_DNS_PORT": lambda value: validate_integer(value, 1, 65535),
    "DNSDIST_WG_NETWORK": validate_network,
    "DNSDIST_WG_INTERFACE": validate_interface,
    "DNSDIST_MIHOMO_ADDRESS": validate_endpoint,
    "DNSDIST_ALIDNS_1": validate_endpoint,
    "DNSDIST_ALIDNS_2": validate_endpoint,
    "DNSDIST_ECS_PREFIX_V4": lambda value: validate_integer(value, 0, 32),
    "DNSDIST_ECS_PREFIX_V6": lambda value: validate_integer(value, 0, 128),
    "DNSDIST_ECS_ALLOW_NON_GLOBAL": validate_bool,
    "DNSDIST_PROXY_URLS": validate_urls,
    "DNSDIST_AD_URL": validate_url,
    "DNSDIST_FETCH_TIMEOUT": validate_positive_number,
    "DNSDIST_MAX_DOWNLOAD_BYTES": lambda value: validate_integer(
        value, 1, 2**63 - 1
    ),
    "DNSDIST_MAX_AD_REGEX_RULES": lambda value: validate_integer(
        value, 0, 2**31 - 1
    ),
    "DNSDIST_MAX_UNSUPPORTED_AD_RATIO": validate_ratio,
}


def parse_wireguard_ipv4(text: str) -> tuple[str, str] | None:
    for candidate in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b", text):
        try:
            interface = ipaddress.ip_interface(candidate)
        except ValueError:
            continue
        if interface.version == 4:
            return str(interface.ip), str(interface.network)
    return None


def _run_command(arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def detect_wireguard_values(
    preferred_interface: str | None = None,
    *,
    config_dir: Path = Path("/etc/wireguard"),
) -> dict[str, str]:
    running = _run_command(("wg", "show", "interfaces")).split()
    config_names = (
        sorted(path.stem for path in config_dir.glob("*.conf"))
        if config_dir.is_dir()
        else []
    )
    candidates = list(dict.fromkeys((*running, *config_names)))
    if not candidates:
        return {}
    if preferred_interface in candidates:
        interface = preferred_interface
    elif len(running) == 1:
        interface = running[0]
    elif "wg-pub" in candidates:
        interface = "wg-pub"
    else:
        interface = candidates[0]

    address = parse_wireguard_ipv4(
        _run_command(("ip", "-o", "-4", "address", "show", "dev", interface))
    )
    config_path = config_dir / f"{interface}.conf"
    if address is None and config_path.is_file():
        try:
            address = parse_wireguard_ipv4(config_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    if address is None:
        return {"DNSDIST_WG_INTERFACE": interface}
    ip, network = address
    return {
        "DNSDIST_WG_INTERFACE": interface,
        "DNSDIST_WG_DNS_IP": ip,
        "DNSDIST_WG_NETWORK": network,
    }


def mihomo_config_candidates(
    arguments: Sequence[str], *, cwd: Path = Path("/")
) -> list[Path]:
    config: str | None = None
    directory: str | None = None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-f", "--config"} and index + 1 < len(arguments):
            config = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--config="):
            config = argument.split("=", 1)[1]
        elif argument in {"-d", "--dir"} and index + 1 < len(arguments):
            directory = arguments[index + 1]
            index += 2
            continue
        elif argument.startswith("--dir="):
            directory = argument.split("=", 1)[1]
        index += 1

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else cwd / path

    if config:
        return [resolve(config)]
    root = resolve(directory) if directory else cwd
    return [root / "config.yaml", root / "config.yml"]


def normalize_mihomo_listen(value: str) -> str | None:
    listen = value.strip().strip("'\"")
    if listen.startswith(":"):
        listen = f"127.0.0.1{listen}"
    elif listen.startswith("*:"):
        listen = f"127.0.0.1:{listen.split(':', 1)[1]}"
    elif listen.startswith("0.0.0.0:") or listen.startswith("[::]:"):
        listen = f"127.0.0.1:{listen.rsplit(':', 1)[1]}"
    try:
        validate_endpoint(listen)
    except ValueError:
        return None
    return listen


def parse_mihomo_dns(text: str) -> str | None:
    dns_indent: int | None = None
    enabled = True
    listen: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if dns_indent is None:
            if re.fullmatch(r"dns\s*:\s*(?:#.*)?", stripped):
                dns_indent = indent
            continue
        if indent <= dns_indent:
            break
        match = re.match(r"(enable|listen)\s*:\s*(.*?)\s*(?:#.*)?$", stripped)
        if not match:
            continue
        name, raw_value = match.groups()
        value = raw_value.strip().strip("'\"")
        if name == "enable":
            enabled = value.lower() in {"true", "yes", "on", "1"}
        elif name == "listen":
            listen = value
    return normalize_mihomo_listen(listen) if enabled and listen else None


def detect_mihomo_values(*, proc_root: Path = Path("/proc")) -> dict[str, str]:
    try:
        processes = sorted(
            (path for path in proc_root.iterdir() if path.name.isdigit()),
            key=lambda path: int(path.name),
        )
    except OSError:
        return {}
    for process in processes:
        try:
            arguments = [
                item.decode(errors="replace")
                for item in (process / "cmdline").read_bytes().split(b"\0")
                if item
            ]
        except OSError:
            continue
        if not arguments or "mihomo" not in Path(arguments[0]).name.lower():
            continue
        try:
            cwd = (process / "cwd").resolve()
        except OSError:
            cwd = Path("/")
        for config in mihomo_config_candidates(arguments, cwd=cwd):
            try:
                address = parse_mihomo_dns(config.read_text(encoding="utf-8"))
            except OSError:
                continue
            if address:
                return {"DNSDIST_MIHOMO_ADDRESS": address}
    return {}


def detect_system_values(
    preferred_interface: str | None = None,
) -> dict[str, str]:
    values = detect_wireguard_values(preferred_interface)
    values.update(detect_mihomo_values())
    return values


def print_detected_values(values: dict[str, str]) -> None:
    interface = values.get("DNSDIST_WG_INTERFACE")
    address = values.get("DNSDIST_WG_DNS_IP")
    network = values.get("DNSDIST_WG_NETWORK")
    if interface:
        detail = f"，地址 {address}，网络 {network}" if address and network else ""
        print(f"检测到 WireGuard：{interface}{detail}")
    if "DNSDIST_MIHOMO_ADDRESS" in values:
        print(f"检测到 Mihomo DNS：{values['DNSDIST_MIHOMO_ADDRESS']}")


def migrate_old_defaults(values: dict[str, str]) -> None:
    for name, (old, new) in MIGRATED_DEFAULTS.items():
        if values.get(name) == old:
            values[name] = new
            print(f"更新默认参数：{name}={old} → {new}")


def prompt_value(name: str, default: str) -> str:
    description = PROMPTS.get(name, name)
    while True:
        entered = input(f"{description} [{default}]: ").strip()
        value = entered or default
        try:
            validator = VALIDATORS.get(name)
            if validator:
                validator(value)
        except (ValueError, TypeError) as exc:
            print(f"输入无效：{exc}")
            continue
        return value


def render_config(
    template_text: str,
    values: dict[str, str],
    *,
    install_dir: Path,
    interactive: bool,
    fixed_values: dict[str, str] | None = None,
) -> str:
    fixed_values = fixed_values or {}
    values.update(fixed_values)
    output: list[str] = []
    template_names: set[str] = set()
    for line in template_text.splitlines():
        match = ASSIGNMENT_RE.match(line.strip())
        if not match:
            output.append(line)
            continue
        name = match.group(1)
        template_names.add(name)
        template_default = parse_value(match.group(2))
        value = values.get(name, template_default)
        if name == "DNSDIST_INSTALL_DIR":
            value = str(install_dir)
        elif interactive and name in INTERACTIVE_NAMES and name not in fixed_values:
            value = prompt_value(name, value)
        else:
            validator = VALIDATORS.get(name)
            if validator:
                validator(value)
        values[name] = value
        output.append(f"{name}={shlex.quote(value)}")

    address = ipaddress.ip_address(values["DNSDIST_WG_DNS_IP"])
    network = ipaddress.ip_network(values["DNSDIST_WG_NETWORK"], strict=False)
    if address not in network:
        raise ValueError("dnsdist 监听地址必须位于配置的 WireGuard 网络内")

    extras = sorted(name for name in values if name not in template_names)
    if extras:
        output.extend(["", "# Values retained from the previous local configuration."])
        output.extend(f"{name}={shlex.quote(values[name])}" for name in extras)
    return "\n".join(output).rstrip() + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o640
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge and optionally prompt for dnsdist local parameters"
    )
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--dns-port")
    parser.add_argument("--detect-system", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    template = args.template.read_text(encoding="utf-8")
    current = read_values(args.current) if args.current else {}
    migrate_old_defaults(current)
    values: dict[str, str] = {}
    if args.detect_system:
        detected = detect_system_values(current.get("DNSDIST_WG_INTERFACE"))
        print_detected_values(detected)
        values.update(detected)
    values.update(current)
    fixed_values = {}
    if args.dns_port is not None:
        fixed_values["DNSDIST_WG_DNS_PORT"] = args.dns_port
    content = render_config(
        template,
        values,
        install_dir=args.install_dir,
        interactive=args.interactive,
        fixed_values=fixed_values,
    )
    atomic_write(args.output, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
