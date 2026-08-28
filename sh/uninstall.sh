#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    '用法：sudo ./sh/uninstall.sh [--purge] [--dry-run]' \
    '' \
    '停止服务并移除系统中的项目软链接。默认保留集中安装目录。' \
    '' \
    '选项：' \
    '  --purge    同时删除完整安装目录' \
    '  --dry-run  仅显示操作，不修改系统' \
    '  -h, --help 显示帮助'
}

purge=0
dry_run=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) purge=1 ;;
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_dir=$(cd -- "${script_dir}/.." && pwd)

if [[ ${dry_run} -eq 0 && ${EUID} -ne 0 ]]; then
  printf '%s\n' '错误：必须使用 root 权限运行；--dry-run 除外' >&2
  exit 1
fi
if [[ ${purge} -eq 1 && ${dry_run} -eq 0 ]]; then
  # shellcheck disable=SC1091
  source "${script_dir}/install-common.sh"
  validate_install_dir "${install_dir}"
fi

print_command() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  if [[ ${dry_run} -eq 1 ]]; then
    print_command "$@"
  else
    "$@"
  fi
}

run_if_possible() {
  if [[ ${dry_run} -eq 1 ]]; then
    print_command "$@"
  else
    "$@" >/dev/null 2>&1 || true
  fi
}

remove_managed_link() {
  local target=$1
  local expected=$2
  if [[ ${dry_run} -eq 1 ]]; then
    printf '+ remove-managed-link %q -> %q\n' "${target}" "${expected}"
    return
  fi
  if [[ -L ${target} && $(readlink "${target}") == "${expected}" ]]; then
    rm -f -- "${target}"
  elif [[ -e ${target} || -L ${target} ]]; then
    printf '跳过非本项目管理的路径：%s\n' "${target}" >&2
  fi
}

timers=(dnsdist-domain-update.timer dnsdist-ecs-update.timer)
run_if_possible systemctl disable --now "${timers[@]}"
run_if_possible systemctl disable --now dnsdist.service

for unit in \
  dnsdist-domain-update.service \
  dnsdist-domain-update.timer \
  dnsdist-ecs-update.service \
  dnsdist-ecs-update.timer; do
  remove_managed_link \
    "/etc/systemd/system/${unit}" \
    "${install_dir}/systemd/${unit}"
done
remove_managed_link \
  /etc/systemd/system/dnsdist.service.d/10-dnsdist-automation.conf \
  "${install_dir}/systemd/10-dnsdist-automation.conf"
remove_managed_link \
  /etc/dnsdist/dnsdist.conf \
  "${install_dir}/config/dnsdist.conf"

if [[ -f ${install_dir}/config/vendor-dnsdist.conf.backup ]]; then
  run mv \
    "${install_dir}/config/vendor-dnsdist.conf.backup" \
    /etc/dnsdist/dnsdist.conf
fi

run_if_possible rmdir /etc/systemd/system/dnsdist.service.d
run_if_possible systemctl daemon-reload
run_if_possible systemctl reset-failed

if [[ ${purge} -eq 1 ]]; then
  run rm -rf -- "${install_dir}"
  printf '%s\n' 'dnsdist 策略网关已卸载，集中安装目录已删除'
else
  printf 'dnsdist 策略网关已卸载，安装目录已保留：%s\n' "${install_dir}"
fi

