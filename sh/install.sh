#!/usr/bin/env bash
set -euo pipefail

DEFAULT_INSTALL_DIR=/opt/mydnsdist
DEFAULT_ARCHIVE_URL=https://github.com/AfxMsgBox/dnsdist/archive/refs/heads/main.tar.gz

if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
  BOOT_RED=$'\033[31m'
  BOOT_YELLOW=$'\033[33m'
  BOOT_CYAN=$'\033[36m'
  BOOT_BOLD=$'\033[1m'
  BOOT_RESET=$'\033[0m'
else
  BOOT_RED=''
  BOOT_YELLOW=''
  BOOT_CYAN=''
  BOOT_BOLD=''
  BOOT_RESET=''
fi

bootstrap_step() {
  printf '%s%s==>%s %s\n' "${BOOT_BOLD}" "${BOOT_CYAN}" "${BOOT_RESET}" "$*"
}

bootstrap_error() {
  printf '%s✗ 错误：%s%s\n' "${BOOT_RED}" "$*" "${BOOT_RESET}" >&2
}

bootstrap_warning() {
  printf '%s!%s %s\n' "${BOOT_YELLOW}" "${BOOT_RESET}" "$*" >&2
}

usage() {
  printf '%s\n' \
    '用法：bash ./install.sh [选项]' \
    '' \
    '仅下载本文件即可完成依赖检查、完整代码下载、参数配置和服务启动。' \
    '' \
    '选项：' \
    '  --install-dir PATH   安装目录，默认 /opt/mydnsdist' \
    '  --dns-port PORT      dnsdist 监听端口，未指定时默认 53' \
    '  --non-interactive    不询问参数，使用已有值、系统检测值或仓库默认值' \
    '  --download-proxy URL 使用指定的 HTTP/HTTPS 下载代理' \
    '  --no-download-proxy  即使检测到 Mihomo 也使用直连' \
    '  --archive-url URL    覆盖 GitHub 源码压缩包地址' \
    '  -h, --help           显示帮助'
}

install_dir=''
dns_port=''
archive_url=${DNSDIST_ARCHIVE_URL:-${DEFAULT_ARCHIVE_URL}}
download_proxy=${DNSDIST_DOWNLOAD_PROXY:-}
download_proxy_mode=auto
[[ -n ${download_proxy} ]] && download_proxy_mode=set
non_interactive=0
deploy_only=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      install_dir=$2
      shift 2
      ;;
    --dns-port)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      dns_port=$2
      shift 2
      ;;
    --non-interactive) non_interactive=1; shift ;;
    --download-proxy)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      download_proxy=$2
      download_proxy_mode=set
      shift 2
      ;;
    --no-download-proxy)
      download_proxy=''
      download_proxy_mode=disabled
      shift
      ;;
    --archive-url)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      archive_url=$2
      shift 2
      ;;
    --deploy-only) deploy_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if [[ -n ${download_proxy} && ! ${download_proxy} =~ ^https?://[^[:space:]]+$ ]]; then
  bootstrap_error '下载代理必须是有效的 HTTP 或 HTTPS URL'
  exit 2
fi

if [[ -n ${dns_port} ]]; then
  if [[ ! ${dns_port} =~ ^[0-9]+$ || ${#dns_port} -gt 5 ]]; then
    bootstrap_error 'dnsdist 监听端口必须是 1 到 65535 的整数'
    exit 2
  fi
  dns_port=$((10#${dns_port}))
  if (( dns_port < 1 || dns_port > 65535 )); then
    bootstrap_error 'dnsdist 监听端口必须是 1 到 65535 的整数'
    exit 2
  fi
fi

if [[ ${EUID} -ne 0 ]]; then
  bootstrap_error '必须使用 root 权限运行安装脚本'
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_root=$(cd -- "${script_dir}/.." && pwd)
common_file=${script_dir}/install-common.sh

read_mihomo_proxy_port() {
  local config=$1
  local line key value mixed_port='' http_port=''
  [[ -f ${config} ]] || return 1
  while IFS= read -r line || [[ -n ${line} ]]; do
    [[ -n ${line} && ${line:0:1} != ' ' && ${line:0:1} != $'\t' ]] || continue
    line=${line%%#*}
    [[ ${line} == *:* ]] || continue
    key=${line%%:*}
    value=${line#*:}
    key=${key//[[:space:]]/}
    value=${value//[[:space:]]/}
    value=${value//\"/}
    value=${value//\'/}
    [[ ${value} =~ ^[0-9]+$ ]] || continue
    (( 10#${value} >= 1 && 10#${value} <= 65535 )) || continue
    case "${key}" in
      mixed-port) mixed_port=$((10#${value})) ;;
      port) http_port=$((10#${value})) ;;
    esac
  done < "${config}"
  if [[ -n ${mixed_port} ]]; then
    printf '%s\n' "${mixed_port}"
  elif [[ -n ${http_port} ]]; then
    printf '%s\n' "${http_port}"
  else
    return 1
  fi
}

detect_mihomo_download_proxy() {
  local cmdline process_dir executable cwd config directory argument port candidate index
  local -a arguments candidates
  for cmdline in /proc/[0-9]*/cmdline; do
    [[ -r ${cmdline} ]] || continue
    arguments=()
    while IFS= read -r -d '' argument; do
      arguments+=("${argument}")
    done < "${cmdline}"
    [[ ${#arguments[@]} -gt 0 ]] || continue
    executable=${arguments[0]##*/}
    [[ ${executable,,} == *mihomo* ]] || continue
    process_dir=${cmdline%/cmdline}
    cwd=$(readlink "${process_dir}/cwd" 2>/dev/null || printf '/')
    config=''
    directory=''
    for ((index = 1; index < ${#arguments[@]}; index++)); do
      case "${arguments[index]}" in
        -f|--config)
          ((index + 1 < ${#arguments[@]})) && config=${arguments[index + 1]}
          ((index++))
          ;;
        --config=*) config=${arguments[index]#*=} ;;
        -d|--dir)
          ((index + 1 < ${#arguments[@]})) && directory=${arguments[index + 1]}
          ((index++))
          ;;
        --dir=*) directory=${arguments[index]#*=} ;;
      esac
    done
    candidates=()
    if [[ -n ${config} ]]; then
      [[ ${config} == /* ]] || config=${cwd}/${config}
      candidates+=("${config}")
    else
      directory=${directory:-${cwd}}
      [[ ${directory} == /* ]] || directory=${cwd}/${directory}
      candidates+=("${directory}/config.yaml" "${directory}/config.yml")
    fi
    for candidate in "${candidates[@]}"; do
      if port=$(read_mihomo_proxy_port "${candidate}"); then
        printf 'http://127.0.0.1:%s\n' "${port}"
        return 0
      fi
    done
  done
  return 1
}

if [[ -z ${install_dir} ]]; then
  install_dir=${DEFAULT_INSTALL_DIR}
  if [[ ${non_interactive} -eq 0 && -t 0 ]]; then
    read -r -p "安装目录 [${DEFAULT_INSTALL_DIR}]: " entered_dir
    install_dir=${entered_dir:-${DEFAULT_INSTALL_DIR}}
  fi
fi
while [[ ${install_dir} != / && ${install_dir} == */ ]]; do
  install_dir=${install_dir%/}
done

# A standalone copy has no supporting files yet. Bootstrap with wget/curl,
# unpack the repository snapshot, then execute the complete installer.
if [[ ! -f ${common_file} ]]; then
  [[ ${install_dir} =~ ^/[A-Za-z0-9._/-]+$ && ${install_dir} != / && ${install_dir} != *//* ]] || {
    bootstrap_error '安装目录必须是仅包含安全字符的绝对路径'
    exit 1
  }
  case "${install_dir}" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      bootstrap_error "安装目录范围过大：${install_dir}"
      exit 1
      ;;
  esac
  [[ ${install_dir} != *'/../'* && ${install_dir} != */.. && ${install_dir} != *'/./'* ]] || {
    bootstrap_error '安装目录不能包含 . 或 .. 路径段'
    exit 1
  }
  if [[ ${download_proxy_mode} == auto && ${non_interactive} -eq 0 && -t 0 ]]; then
    detected_proxy=$(detect_mihomo_download_proxy || true)
    if [[ -n ${detected_proxy} ]]; then
      while true; do
        read -r -p \
          "检测到 Mihomo HTTP/混合代理 ${detected_proxy}，是否用于下载？[Y/n]: " \
          use_proxy
        case "${use_proxy,,}" in
          ''|y|yes)
            download_proxy=${detected_proxy}
            download_proxy_mode=set
            break
            ;;
          n|no)
            download_proxy=''
            download_proxy_mode=disabled
            break
            ;;
          *) bootstrap_warning '请输入 y 或 n' ;;
        esac
      done
    fi
  fi
  bootstrap_commands=()
  bootstrap_packages=()
  if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    bootstrap_commands+=('wget 或 curl')
    bootstrap_packages+=(wget ca-certificates)
  fi
  if ! command -v tar >/dev/null 2>&1; then
    bootstrap_commands+=(tar)
    bootstrap_packages+=(tar)
  fi
  if ! command -v sha256sum >/dev/null 2>&1; then
    bootstrap_commands+=(sha256sum)
    bootstrap_packages+=(coreutils)
  fi
  if [[ ${#bootstrap_packages[@]} -gt 0 ]]; then
    bootstrap_error "缺少引导安装依赖：${bootstrap_commands[*]}"
    if command -v apt-get >/dev/null 2>&1; then
      bootstrap_warning \
        "请手动执行 apt-get update && apt-get install -y ${bootstrap_packages[*]}，然后重新运行本脚本。"
    else
      bootstrap_warning \
        "请使用当前系统的软件包管理器安装：${bootstrap_packages[*]}，然后重新运行本脚本。"
    fi
    exit 1
  fi
  temporary_dir=$(mktemp -d)
  trap 'rm -rf -- "${temporary_dir}"' EXIT
  archive=${temporary_dir}/dnsdist.tar.gz
  extracted=${temporary_dir}/source
  bootstrap_step '下载 dnsdist 策略网关程序包'
  if command -v wget >/dev/null 2>&1; then
    if [[ -n ${download_proxy} ]]; then
      http_proxy="${download_proxy}" https_proxy="${download_proxy}" \
        no_proxy='' NO_PROXY='' \
        wget --quiet --timeout=30 --tries=3 \
          --output-document="${archive}" "${archive_url}"
    else
      wget --no-proxy --quiet --timeout=30 --tries=3 \
        --output-document="${archive}" "${archive_url}"
    fi
  else
    bootstrap_proxy_arguments=(--proxy '')
    [[ -n ${download_proxy} ]] && \
      bootstrap_proxy_arguments=(--proxy "${download_proxy}" --noproxy '')
    curl --fail --location --silent --show-error \
      --connect-timeout 30 --retry 3 "${bootstrap_proxy_arguments[@]}" \
      --output "${archive}" "${archive_url}"
  fi
  mkdir -p "${extracted}"
  tar -xzf "${archive}" --strip-components=1 -C "${extracted}"
  [[ -f ${extracted}/sh/install-common.sh ]] || {
    bootstrap_error '下载的软件包结构不完整'
    exit 1
  }
  sha256sum "${archive}" | awk '{print $1}' > "${extracted}/.source-sha256"
  arguments=(--install-dir "${install_dir}" --archive-url "${archive_url}")
  [[ -n ${dns_port} ]] && arguments+=(--dns-port "${dns_port}")
  [[ ${non_interactive} -eq 1 ]] && arguments+=(--non-interactive)
  if [[ ${download_proxy_mode} == set ]]; then
    arguments+=(--download-proxy "${download_proxy}")
  elif [[ ${download_proxy_mode} == disabled ]]; then
    arguments+=(--no-download-proxy)
  fi
  "${extracted}/sh/install.sh" "${arguments[@]}"
  exit
fi

# shellcheck disable=SC1090
source "${common_file}"
log_step '检查运行环境'
require_root
validate_install_dir "${install_dir}"
ensure_dependencies
check_base_environment
log_success '运行环境检查通过'

deployment_work_dir=''
deployment_previous_root=''
deployment_swapped=0
existing_installation=0

cleanup_deployment() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 && ${deployment_swapped} -eq 1 ]]; then
    log_warning '安装失败，正在恢复原安装目录……'
    if [[ -e ${install_dir} ]]; then
      mv "${install_dir}" "${deployment_work_dir}/failed"
    fi
    mv "${deployment_previous_root}" "${install_dir}"
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  if [[ -n ${deployment_work_dir} && -d ${deployment_work_dir} ]]; then
    rm -rf -- "${deployment_work_dir}"
  fi
  exit "${status}"
}
trap cleanup_deployment EXIT

deploy_source_tree() {
  local source=$1
  local destination=$2
  local parent_dir base_name next_root item generated_file
  parent_dir=$(dirname -- "${destination}")
  base_name=$(basename -- "${destination}")
  mkdir -p "${parent_dir}"
  deployment_work_dir=$(mktemp -d "${parent_dir}/.${base_name}.install.XXXXXX")
  next_root=${deployment_work_dir}/next
  mkdir -p "${next_root}"
  for item in ARCH.md README.md TODO.md config generated sh systemd; do
    cp -a "${source}/${item}" "${next_root}/"
  done
  if [[ -f ${source}/.source-sha256 ]]; then
    cp -p "${source}/.source-sha256" "${next_root}/.source-sha256"
  fi
  validate_source "${next_root}"

  if [[ -e ${destination} && ! -d ${destination} ]]; then
    die "安装路径已存在且不是目录：${destination}"
  fi
  if [[ -d ${destination} ]] && \
    [[ -n $(find "${destination}" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
    source_is_complete "${destination}" || \
      die "安装目录非空且不是可识别的完整安装：${destination}"
    existing_installation=1
    log_info "检测到已有安装，保留本机配置并刷新程序文件：${destination}"
    python3 "${next_root}/sh/manage-config.py" \
      --template "${next_root}/config/dnsdist-automation" \
      --current "${destination}/config/dnsdist-automation" \
      --output "${next_root}/config/dnsdist-automation" \
      --install-dir "${destination}"
    for generated_file in domain-rules.lua ecs-rules.lua; do
      if [[ -f ${destination}/generated/${generated_file} ]]; then
        cp -p \
          "${destination}/generated/${generated_file}" \
          "${next_root}/generated/${generated_file}"
      fi
    done
    if [[ -f ${destination}/config/vendor-dnsdist.conf.backup ]]; then
      cp -p \
        "${destination}/config/vendor-dnsdist.conf.backup" \
        "${next_root}/config/vendor-dnsdist.conf.backup"
    fi
    deployment_previous_root=${deployment_work_dir}/previous
    mv "${destination}" "${deployment_previous_root}"
    mv "${next_root}" "${destination}"
    deployment_swapped=1
  else
    rmdir "${destination}" 2>/dev/null || true
    mv "${next_root}" "${destination}"
  fi
}

if [[ ${deploy_only} -eq 0 && ${source_root} != "${install_dir}" ]]; then
  log_step '部署程序文件'
  deploy_source_tree "${source_root}" "${install_dir}"
  log_success "程序文件已部署到 ${install_dir}"
elif [[ ${source_root} == "${install_dir}" ]]; then
  existing_installation=1
fi

source_root=${install_dir}
validate_source "${source_root}"

config_file=${source_root}/config/dnsdist-automation
config_current=''
if [[ ${existing_installation} -eq 1 ]]; then
  config_current=${config_file}
fi
if [[ ${existing_installation} -eq 0 && \
      -f /etc/default/dnsdist-automation && \
      ! -L /etc/default/dnsdist-automation ]]; then
  log_info '检测到旧版本机参数，将迁移到集中安装目录。'
  config_current=/etc/default/dnsdist-automation
fi
config_arguments=(
  --template "${config_file}"
  --output "${config_file}"
  --install-dir "${source_root}"
  --detect-system
)
[[ -n ${config_current} ]] && config_arguments+=(--current "${config_current}")
[[ -n ${dns_port} ]] && config_arguments+=(--dns-port "${dns_port}")
if [[ ${download_proxy_mode} == set ]]; then
  config_arguments+=(--download-proxy "${download_proxy}")
elif [[ ${download_proxy_mode} == disabled ]]; then
  config_arguments+=(--no-download-proxy)
fi
if [[ ${non_interactive} -eq 0 && -t 0 ]]; then
  log_step '确认 dnsdist 运行参数；直接回车使用方括号中的默认值'
  config_arguments+=(--interactive)
fi
python3 "${source_root}/sh/manage-config.py" "${config_arguments[@]}"
load_local_config "${source_root}"

log_step '检查 WireGuard、Mihomo 与监听端口'
if ! wg show "${DNSDIST_WG_INTERFACE}" >/dev/null 2>&1; then
  die "WireGuard 接口不可用：${DNSDIST_WG_INTERFACE}"
fi
if ! ip -o address show | awk '{print $4}' | cut -d/ -f1 | \
  grep -Fqx -- "${DNSDIST_WG_DNS_IP}"; then
  die "dnsdist 监听地址尚未分配到本机：${DNSDIST_WG_DNS_IP}"
fi
check_dns_listener_available "${DNSDIST_WG_DNS_IP}" "${DNSDIST_WG_DNS_PORT}"
check_mihomo_listener "${DNSDIST_MIHOMO_ADDRESS}"
log_success '网络运行环境检查通过'

dnsdist_user=$(systemctl show dnsdist.service --property=User --value 2>/dev/null || true)
dnsdist_group=$(systemctl show dnsdist.service --property=Group --value 2>/dev/null || true)
if [[ -z ${dnsdist_user} ]]; then
  for candidate in _dnsdist dnsdist; do
    if id "${candidate}" >/dev/null 2>&1; then
      dnsdist_user=${candidate}
      break
    fi
  done
fi
if [[ -z ${dnsdist_group} && -n ${dnsdist_user} ]]; then
  dnsdist_group=$(id -gn "${dnsdist_user}")
fi
if [[ -z ${dnsdist_group} ]] || ! getent group "${dnsdist_group}" >/dev/null; then
  die '无法识别 dnsdist 服务运行组'
fi

if [[ -e /etc/dnsdist/dnsdist.yml ]]; then
  die '/etc/dnsdist/dnsdist.yml 可能优先于 Lua 配置，请先明确处理该文件'
fi

chmod 0755 "${source_root}" "${source_root}/sh"
chmod 0755 "${source_root}"/sh/*.sh "${source_root}"/sh/*.py
chmod 0644 "${source_root}"/sh/dnsdist_automation/*.py
install -d -m 0750 -o root -g "${dnsdist_group}" "${source_root}/config"
install -d -m 2750 -o root -g "${dnsdist_group}" "${source_root}/generated"
chown root:"${dnsdist_group}" \
  "${source_root}/config/dnsdist.conf" \
  "${source_root}/config/dnsdist-automation" \
  "${source_root}/generated/domain-rules.lua" \
  "${source_root}/generated/ecs-rules.lua"
chmod 0640 \
  "${source_root}/config/dnsdist.conf" \
  "${source_root}/config/dnsdist-automation" \
  "${source_root}/generated/domain-rules.lua" \
  "${source_root}/generated/ecs-rules.lua"

render_systemd_units "${source_root}"
chmod 0644 "${source_root}"/systemd/*

install -d -m 0755 /etc/dnsdist /etc/systemd/system/dnsdist.service.d
if [[ -e /etc/dnsdist/dnsdist.conf && ! -L /etc/dnsdist/dnsdist.conf ]]; then
  if [[ ! -e ${source_root}/config/vendor-dnsdist.conf.backup ]]; then
    mv /etc/dnsdist/dnsdist.conf "${source_root}/config/vendor-dnsdist.conf.backup"
  else
    die '/etc/dnsdist/dnsdist.conf 已存在，且供应商配置备份也已存在'
  fi
fi
safe_managed_link "${source_root}/config/dnsdist.conf" /etc/dnsdist/dnsdist.conf
safe_managed_link \
  "${source_root}/systemd/10-dnsdist-automation.conf" \
  /etc/systemd/system/dnsdist.service.d/10-dnsdist-automation.conf
for unit in \
  dnsdist-domain-update.service \
  dnsdist-domain-update.timer \
  dnsdist-ecs-update.service \
  dnsdist-ecs-update.timer; do
  safe_managed_link \
    "${source_root}/systemd/${unit}" \
    "/etc/systemd/system/${unit}"
done

systemctl daemon-reload
log_step '生成规则并验证 dnsdist 配置'
"${source_root}/sh/update-dnsdist-domains.py" --no-check --no-reload
"${source_root}/sh/update-dnsdist-ecs.py" --no-check --no-reload
dnsdist --check-config -C "${source_root}/config/dnsdist.conf"
systemctl enable --now dnsdist.service
if [[ ${existing_installation} -eq 1 ]]; then
  systemctl restart dnsdist.service
fi
systemctl enable --now \
  dnsdist-domain-update.timer \
  dnsdist-ecs-update.timer

log_success "dnsdist 策略网关安装成功：${source_root}"
