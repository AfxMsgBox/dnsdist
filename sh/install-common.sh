#!/usr/bin/env bash

DNSDIST_REPOSITORY_ARCHIVE_DEFAULT="https://github.com/AfxMsgBox/dnsdist/archive/refs/heads/main.tar.gz"

die() {
  printf '错误：%s\n' "$*" >&2
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
    'wg:wireguard-tools'
    'python3:python3'
    'systemctl:systemd'
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
  printf '错误：缺少必要命令：%s\n' "${missing_commands[*]}" >&2
  if command -v apt-get >/dev/null 2>&1; then
    printf '提示：请手动执行 apt-get update && apt-get install -y %s，然后重新运行本脚本。\n' \
      "${packages[*]}" >&2
  else
    printf '提示：请使用当前系统的软件包管理器安装：%s，然后重新运行本脚本。\n' \
      "${packages[*]}" >&2
  fi
  return 1
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
    systemd/10-dnsdist-automation.conf \
    tests/test_common.py \
    tests/test_domains.py \
    tests/test_ecs.py \
    tests/test_install.py \
    tests/test_manage_config.py \
    tests/test_uninstall.py \
    tests/test_update.py
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
  bash -n \
    "${root}/sh/install.sh" \
    "${root}/sh/update.sh" \
    "${root}/sh/uninstall.sh" \
    "${root}/sh/install-common.sh"
  env PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/dnsdist-python-cache" \
    python3 -m compileall -q "${root}/sh" "${root}/tests"
  env PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/dnsdist-python-cache" \
    python3 -m unittest discover -s "${root}/tests" -v
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
  [[ ! -e ${target} ]] || die "拒绝覆盖已有文件：${target}"
  ln -s "${source}" "${target}"
}

load_local_config() {
  local root=$1
  set -a
  # shellcheck disable=SC1090
  source "${root}/config/dnsdist-automation"
  set +a
}
