#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    '用法：sudo ./sh/update.sh [选项]' \
    '' \
    '从 GitHub 压缩包更新全部程序文件，保留本机参数并补充新增参数。' \
    '' \
    '选项：' \
    '  --configure         更新时重新逐项确认本机参数' \
    '  --archive-url URL   覆盖 GitHub 源码压缩包地址' \
    '  -h, --help          显示帮助'
}

configure=0
archive_url=${DNSDIST_ARCHIVE_URL:-https://github.com/AfxMsgBox/dnsdist/archive/refs/heads/main.tar.gz}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --configure) configure=1; shift ;;
    --archive-url)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      archive_url=$2
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_dir=$(cd -- "${script_dir}/.." && pwd)

# shellcheck disable=SC1091
source "${script_dir}/install-common.sh"
log_step '检查更新环境'
require_root
validate_install_dir "${install_dir}"
ensure_dependencies
check_base_environment
source_is_complete "${install_dir}" || die '当前安装目录结构不完整'
log_success '更新环境检查通过'

if [[ ${configure} -eq 1 && ! -t 0 ]]; then
  die '--configure 需要交互式终端'
fi

parent_dir=$(dirname -- "${install_dir}")
base_name=$(basename -- "${install_dir}")
work_dir=$(mktemp -d "${parent_dir}/.${base_name}.update.XXXXXX")
archive=${work_dir}/dnsdist.tar.gz
next_root=${work_dir}/next
previous_root=${work_dir}/previous
failed_root=${work_dir}/failed
swapped=0

cleanup() {
  if [[ ${swapped} -eq 0 ]]; then
    rm -rf -- "${work_dir}"
  fi
}
trap cleanup EXIT

log_step '下载最新程序包'
download_file "${archive_url}" "${archive}"
new_checksum=$(sha256sum "${archive}" | awk '{print $1}')
old_checksum=''
if [[ -f ${install_dir}/.source-sha256 ]]; then
  old_checksum=$(<"${install_dir}/.source-sha256")
fi
if [[ ${new_checksum} == "${old_checksum}" && ${configure} -eq 0 ]]; then
  log_success '当前已经是最新版本'
  exit 0
fi

extract_archive "${archive}" "${next_root}"
validate_source "${next_root}"
remove_development_files "${next_root}"

config_arguments=(
  --template "${next_root}/config/dnsdist-automation"
  --current "${install_dir}/config/dnsdist-automation"
  --output "${next_root}/config/dnsdist-automation"
  --install-dir "${install_dir}"
)
if [[ ${configure} -eq 1 ]]; then
  log_step '确认 dnsdist 运行参数；直接回车保留当前值'
  config_arguments+=(--interactive)
fi
python3 "${next_root}/sh/manage-config.py" "${config_arguments[@]}"

for generated_file in domain-rules.lua ecs-rules.lua; do
  if [[ -f ${install_dir}/generated/${generated_file} ]]; then
    cp -p \
      "${install_dir}/generated/${generated_file}" \
      "${next_root}/generated/${generated_file}"
  fi
done
if [[ -f ${install_dir}/config/vendor-dnsdist.conf.backup ]]; then
  cp -p \
    "${install_dir}/config/vendor-dnsdist.conf.backup" \
    "${next_root}/config/vendor-dnsdist.conf.backup"
fi
printf '%s\n' "${new_checksum}" > "${next_root}/.source-sha256"
# Unit files live in the staging tree for validation, but every embedded path
# must already point to the final installation directory used after the swap.
render_systemd_units "${next_root}" "${install_dir}"

dnsdist_group=$(stat -c '%G' "${install_dir}/generated")
getent group "${dnsdist_group}" >/dev/null || die '无法识别现有 dnsdist 服务组'
chmod 0755 "${next_root}" "${next_root}/sh"
chmod 0755 "${next_root}"/sh/*.sh "${next_root}"/sh/*.py
chmod 0644 "${next_root}"/sh/dnsdist_automation/*.py "${next_root}"/systemd/*
chown root:"${dnsdist_group}" "${next_root}/config" "${next_root}/generated"
chmod 0750 "${next_root}/config"
chmod 2750 "${next_root}/generated"
chown root:"${dnsdist_group}" \
  "${next_root}/config/dnsdist.conf" \
  "${next_root}/config/dnsdist-automation" \
  "${next_root}/generated/domain-rules.lua" \
  "${next_root}/generated/ecs-rules.lua"
chmod 0640 \
  "${next_root}/config/dnsdist.conf" \
  "${next_root}/config/dnsdist-automation" \
  "${next_root}/generated/domain-rules.lua" \
  "${next_root}/generated/ecs-rules.lua"

set -a
# shellcheck disable=SC1090
source "${next_root}/config/dnsdist-automation"
set +a
check_mihomo_listener "${DNSDIST_MIHOMO_ADDRESS}"
log_step '使用新版本生成并验证规则'
DNSDIST_INSTALL_DIR=${next_root} \
  "${next_root}/sh/update-dnsdist-domains.py" --no-check --no-reload
DNSDIST_INSTALL_DIR=${next_root} \
  "${next_root}/sh/update-dnsdist-ecs.py" --no-check --no-reload
DNSDIST_INSTALL_DIR=${next_root} \
  dnsdist --check-config -C "${next_root}/config/dnsdist.conf"

rollback() {
  log_warning '新版本启动失败，正在恢复上一版本……'
  systemctl stop dnsdist-domain-update.timer dnsdist-ecs-update.timer dnsdist.service \
    >/dev/null 2>&1 || true
  if [[ -d ${install_dir} ]]; then
    mv "${install_dir}" "${failed_root}"
  fi
  mv "${previous_root}" "${install_dir}"
  systemctl daemon-reload
  systemctl restart dnsdist.service >/dev/null 2>&1 || true
  systemctl start dnsdist-domain-update.timer dnsdist-ecs-update.timer \
    >/dev/null 2>&1 || true
}

report_activation_failure() {
  log_warning '新版本激活失败，回滚前输出 dnsdist 诊断信息：'
  systemctl status dnsdist.service --no-pager --full || true
  journalctl --unit dnsdist.service --lines 80 --no-pager || true
}

systemctl stop dnsdist-domain-update.timer dnsdist-ecs-update.timer
systemctl stop dnsdist.service
cd "${parent_dir}"
mv "${install_dir}" "${previous_root}"
mv "${next_root}" "${install_dir}"
swapped=1

set +e
(
  set -e
  set -a
  # shellcheck disable=SC1090
  source "${install_dir}/config/dnsdist-automation"
  set +a
  systemctl daemon-reload
  dnsdist --check-config -C "${install_dir}/config/dnsdist.conf"
  systemctl restart dnsdist.service
  systemctl enable --now \
    dnsdist-domain-update.timer \
    dnsdist-ecs-update.timer
)
activation_status=$?
set -e

if [[ ${activation_status} -ne 0 ]]; then
  report_activation_failure
  rollback
  swapped=0
  die '更新失败，已恢复上一版本'
fi

rm -rf -- "${previous_root}" "${work_dir}"
swapped=0
trap - EXIT
log_success "dnsdist 策略网关更新成功：${install_dir}"
