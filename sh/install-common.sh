#!/usr/bin/env bash

DNSDIST_REPOSITORY_ARCHIVE_DEFAULT="https://github.com/AfxMsgBox/dnsdist/archive/refs/heads/main.tar.gz"

if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
  COLOR_RED=$'\033[31m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_CYAN=$'\033[36m'
  COLOR_BOLD=$'\033[1m'
  COLOR_RESET=$'\033[0m'
else
  COLOR_RED=''
  COLOR_GREEN=''
  COLOR_YELLOW=''
  COLOR_CYAN=''
  COLOR_BOLD=''
  COLOR_RESET=''
fi

log_step() {
  printf '%s%s==>%s %s\n' "${COLOR_BOLD}" "${COLOR_CYAN}" "${COLOR_RESET}" "$*"
}

log_success() {
  printf '%s✓%s %s\n' "${COLOR_GREEN}" "${COLOR_RESET}" "$*"
}

log_warning() {
  printf '%s!%s %s\n' "${COLOR_YELLOW}" "${COLOR_RESET}" "$*" >&2
}

log_info() {
  printf '  %s•%s %s\n' "${COLOR_CYAN}" "${COLOR_RESET}" "$*"
}

die() {
  printf '%s✗ 错误：%s%s\n' "${COLOR_RED}" "$*" "${COLOR_RESET}" >&2
  exit 1
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    die '必须使用 root 权限运行'
  fi
}

validate_install_dir() {
  local path=$1
  [[ ${path} == /* ]] || die '安装目录必须是绝对路径'
  [[ ${path} =~ ^/[A-Za-z0-9._/-]+$ ]] || \
    die '安装目录只能包含字母、数字、点、下划线、连字符和斜杠'
  [[ ${path} != *//* ]] || die '安装目录不能包含重复斜杠'
  [[ ${path} != *'/../'* && ${path} != */.. && ${path} != *'/./'* ]] || \
    die '安装目录不能包含 . 或 .. 路径段'
  case "${path}" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      die "安装目录范围过大：${path}"
      ;;
  esac
}

download_file() {
  local url=$1
  local output=$2
  if command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=30 --tries=3 --output-document="${output}" "${url}"
  elif command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error \
      --connect-timeout 30 --retry 3 --output "${output}" "${url}"
  else
    die '缺少 wget 或 curl'
  fi
}

ensure_dependencies() {
  local -a missing_commands=()
  local -a packages=()
  local command_name package_name requirement
  local -A package_seen=()
  local -a requirements=(
    'dnsdist:dnsdist'
    'ip:iproute2'
    'ss:iproute2'
    'wg:wireguard-tools'
    'python3:python3'
    'systemctl:systemd'
    'getent:libc-bin'
    'install:coreutils'
    'sha256sum:coreutils'
    'tar:tar'
  )

  if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    missing_commands+=('wget 或 curl')
    packages+=(wget ca-certificates)
    package_seen[wget]=1
    package_seen[ca-certificates]=1
  fi
  for requirement in "${requirements[@]}"; do
    command_name=${requirement%%:*}
    package_name=${requirement#*:}
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing_commands+=("${command_name}")
      if [[ ! -v package_seen["${package_name}"] ]]; then
        packages+=("${package_name}")
        package_seen["${package_name}"]=1
      fi
    fi
  done
  if [[ ${#missing_commands[@]} -eq 0 ]]; then
    return
  fi
  log_warning "缺少必要命令：${missing_commands[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    log_warning \
      "请手动执行 apt-get update && apt-get install -y ${packages[*]}，然后重新运行本脚本。"
  else
    log_warning \
      "请使用当前系统的软件包管理器安装：${packages[*]}，然后重新运行本脚本。"
  fi
  return 1
}

check_base_environment() {
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || \
    die '需要 Python 3.10 或更高版本'
  [[ -d /run/systemd/system ]] || die '当前系统未使用 systemd 作为服务管理器'
}

required_source_paths() {
  printf '%s\n' \
    ARCH.md \
    README.md \
    TODO.md \
    config/dnsdist-automation \
    config/dnsdist.conf \
    generated/domain-rules.lua \
    generated/ecs-rules.lua \
    sh/install.sh \
    sh/install-common.sh \
    sh/update.sh \
    sh/uninstall.sh \
    sh/manage-config.py \
    sh/update-dnsdist-domains.py \
    sh/update-dnsdist-ecs.py \
    sh/dnsdist_automation/common.py \
    sh/dnsdist_automation/domains.py \
    sh/dnsdist_automation/ecs.py \
    systemd/dnsdist-domain-update.service \
    systemd/dnsdist-domain-update.timer \
    systemd/dnsdist-ecs-update.service \
    systemd/dnsdist-ecs-update.timer \
    systemd/10-dnsdist-automation.conf
}

source_is_complete() {
  local root=$1
  local relative
  while IFS= read -r relative; do
    [[ -e ${root}/${relative} ]] || return 1
  done < <(required_source_paths)
}

validate_source() {
  local root=$1
  source_is_complete "${root}" || die '下载的软件包结构不完整'
}

remove_development_files() {
  local root=$1
  rm -rf -- "${root}/tests" "${root}/.github"
}

extract_archive() {
  local archive=$1
  local destination=$2
  mkdir -p "${destination}"
  tar -xzf "${archive}" --strip-components=1 -C "${destination}"
}

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

render_systemd_units() {
  local root=$1
  local escaped unit
  escaped=$(escape_sed_replacement "${root}")
  for unit in "${root}"/systemd/*; do
    [[ -f ${unit} ]] || continue
    sed -i "s|@INSTALL_DIR@|${escaped}|g" "${unit}"
  done
}

safe_managed_link() {
  local source=$1
  local target=$2
  if [[ -L ${target} ]]; then
    local current
    current=$(readlink "${target}")
    [[ ${current} == "${source}" ]] || \
      die "拒绝覆盖其他软链接：${target} -> ${current}"
    return
  fi
  if [[ -e ${target} ]]; then
    if legacy_systemd_file_is_managed "${target}"; then
      log_info "迁移旧版项目文件：${target}"
      rm -f -- "${target}"
    else
      die "拒绝覆盖已有文件：${target}"
    fi
  fi
  ln -s "${source}" "${target}"
}

legacy_systemd_file_is_managed() {
  local path=$1
  local content
  [[ -f ${path} && ! -L ${path} ]] || return 1
  content=$(<"${path}")
  case "$(basename -- "${path}")" in
    10-dnsdist-automation.conf)
      [[ ${content} == *'EnvironmentFile=-/etc/default/dnsdist-automation'* ]]
      ;;
    dnsdist-domain-update.service)
      [[ ${content} == *'ExecStart=/usr/local/sbin/update-dnsdist-domains.py'* && \
         ${content} == *'EnvironmentFile=-/etc/default/dnsdist-automation'* ]]
      ;;
    dnsdist-ecs-update.service)
      [[ ${content} == *'ExecStart=/usr/local/sbin/update-dnsdist-ecs.py'* && \
         ${content} == *'EnvironmentFile=-/etc/default/dnsdist-automation'* ]]
      ;;
    dnsdist-domain-update.timer)
      [[ ${content} == *'Description=Update dnsdist domain rules every six hours'* && \
         ${content} == *'Unit=dnsdist-domain-update.service'* ]]
      ;;
    dnsdist-ecs-update.timer)
      [[ ${content} == *'Description=Check WireGuard endpoint changes every minute'* && \
         ${content} == *'Unit=dnsdist-ecs-update.service'* ]]
      ;;
    *) return 1 ;;
  esac
}

dns_listener_conflicts() {
  local listen_ip=$1
  local listen_port=$2
  local local_address=$3
  local process_info=${4:-}
  [[ ${process_info} == *'("dnsdist",'* ]] && return 1
  case "${local_address}" in
    "*:${listen_port}"|"0.0.0.0:${listen_port}"|"[::]:${listen_port}"|\
      "${listen_ip}:${listen_port}"|"[${listen_ip}]:${listen_port}") return 0 ;;
    *) return 1 ;;
  esac
}

check_dns_listener_available() {
  local listen_ip=$1
  local listen_port=$2
  local protocol state recv_q send_q local_address peer_address process_info
  local -a conflicts=()
  while read -r \
    protocol state recv_q send_q local_address peer_address process_info; do
    [[ -n ${local_address:-} ]] || continue
    if dns_listener_conflicts \
      "${listen_ip}" "${listen_port}" "${local_address}" "${process_info:-}"; then
      conflicts+=("${protocol} ${local_address} ${process_info:-未知进程}")
    fi
  done < <(ss -H -lntup "sport = :${listen_port}" 2>/dev/null || true)
  if [[ ${#conflicts[@]} -eq 0 ]]; then
    return
  fi
  log_warning "dnsdist 监听地址 ${listen_ip}:${listen_port} 已被其他进程占用："
  printf '  %s\n' "${conflicts[@]}" >&2
  log_warning '请先调整或停止占用程序；安装器不会自动停止其他服务。'
  return 1
}

check_mihomo_listener() {
  local endpoint=$1
  local host port state recv_q send_q local_address peer_address process_info
  if [[ ${endpoint} == \[*\]:* ]]; then
    host=${endpoint#\[}
    host=${host%%\]*}
    port=${endpoint##*:}
  else
    host=${endpoint%:*}
    port=${endpoint##*:}
  fi
  case "${host}" in
    127.0.0.1|localhost|::1) ;;
    *)
      log_warning "Mihomo DNS 不是本机回环地址，跳过监听检查：${endpoint}"
      return
      ;;
  esac
  while read -r \
    state recv_q send_q local_address peer_address process_info; do
    case "${local_address}" in
      "*:${port}"|"0.0.0.0:${port}"|"[::]:${port}"|\
        "127.0.0.1:${port}"|"[::1]:${port}")
        return
        ;;
    esac
  done < <(ss -H -lnup "sport = :${port}" 2>/dev/null || true)
  die "Mihomo DNS 未监听 UDP ${endpoint}"
}

load_local_config() {
  local root=$1
  set -a
  # shellcheck disable=SC1090
  source "${root}/config/dnsdist-automation"
  set +a
}
