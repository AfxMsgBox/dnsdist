#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    'Usage: sudo ./sh/install.sh [--install-packages]' \
    '' \
    'Copies the repository files into /etc and /usr/local, generates the' \
    'initial rules, validates dnsdist.conf, and enables the timers.'
}

install_packages=0
case "${1:-}" in
  "") ;;
  --install-packages) install_packages=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [[ ${EUID} -ne 0 ]]; then
  printf '%s\n' 'install.sh must run as root' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_dir="$(cd -- "${script_dir}/.." && pwd)"
system_tree="${repository_dir}/sys"
defaults_file="${system_tree}/etc/default/dnsdist-automation"
effective_defaults_file="${defaults_file}"
if [[ -e /etc/default/dnsdist-automation ]]; then
  effective_defaults_file=/etc/default/dnsdist-automation
fi

required_paths=(
  "${defaults_file}"
  "${system_tree}/etc/dnsdist/dnsdist.conf"
  "${system_tree}/etc/dnsdist/generated/domain-rules.lua"
  "${system_tree}/etc/dnsdist/generated/ecs-rules.lua"
  "${system_tree}/etc/systemd/system/dnsdist-domain-update.service"
  "${system_tree}/etc/systemd/system/dnsdist-ecs-update.service"
  "${script_dir}/dnsdist_automation/common.py"
  "${script_dir}/dnsdist_automation/domains.py"
  "${script_dir}/dnsdist_automation/ecs.py"
  "${script_dir}/update-dnsdist-domains.py"
  "${script_dir}/update-dnsdist-ecs.py"
)
for required_path in "${required_paths[@]}"; do
  if [[ ! -e ${required_path} ]]; then
    printf '%s\n' \
      'incomplete repository: required project files are missing.' \
      'Clone the complete repository and run ./sh/install.sh from that checkout.' >&2
    exit 1
  fi
done

if [[ ${install_packages} -eq 1 ]]; then
  apt-get update
  apt-get install -y dnsdist wireguard-tools python3
fi

for command in dnsdist ip wg python3 systemctl install; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf 'required command is missing: %s\n' "${command}" >&2
    exit 1
  fi
done

# Load the same defaults that will be installed for the services, so all
# deployment-specific prerequisites are checked before changing system files.
set -a
# shellcheck disable=SC1090
source "${effective_defaults_file}"
set +a

if ! wg show "${DNSDIST_WG_INTERFACE}" >/dev/null 2>&1; then
  printf 'WireGuard interface is unavailable: %s\n' "${DNSDIST_WG_INTERFACE}" >&2
  exit 1
fi

if ! ip -o address show | awk '{print $4}' | cut -d/ -f1 | \
  grep -Fqx -- "${DNSDIST_WG_DNS_IP}"; then
  printf 'dnsdist listener address is not assigned locally: %s\n' \
    "${DNSDIST_WG_DNS_IP}" >&2
  exit 1
fi

dnsdist_user="$(systemctl show dnsdist.service --property=User --value 2>/dev/null || true)"
dnsdist_group="$(systemctl show dnsdist.service --property=Group --value 2>/dev/null || true)"
if [[ -z ${dnsdist_user} ]]; then
  for candidate in _dnsdist dnsdist; do
    if id "${candidate}" >/dev/null 2>&1; then
      dnsdist_user="${candidate}"
      break
    fi
  done
fi
if [[ -z ${dnsdist_group} && -n ${dnsdist_user} ]]; then
  dnsdist_group="$(id -gn "${dnsdist_user}")"
fi
if [[ -z ${dnsdist_group} ]] || ! getent group "${dnsdist_group}" >/dev/null; then
  printf '%s\n' 'unable to determine the dnsdist service group' >&2
  exit 1
fi

if [[ -e /etc/dnsdist/dnsdist.yml ]]; then
  printf '%s\n' \
    '/etc/dnsdist/dnsdist.yml exists and may take precedence over the Lua config.' \
    'Move or merge it deliberately before running this installer.' >&2
  exit 1
fi

install -d -m 2750 -o root -g "${dnsdist_group}" /etc/dnsdist/generated
install -d -m 0755 /etc/systemd/system/dnsdist.service.d
install -d -m 0755 /usr/local/lib/dnsdist-automation/dnsdist_automation

install -m 0640 -o root -g "${dnsdist_group}" \
  "${system_tree}/etc/dnsdist/dnsdist.conf" \
  /etc/dnsdist/dnsdist.conf

if [[ ! -e /etc/default/dnsdist-automation ]]; then
  install -m 0644 \
    "${system_tree}/etc/default/dnsdist-automation" \
    /etc/default/dnsdist-automation
fi

for seed in domain-rules.lua ecs-rules.lua; do
  if [[ ! -e "/etc/dnsdist/generated/${seed}" ]]; then
    install -m 0640 -o root -g "${dnsdist_group}" \
      "${system_tree}/etc/dnsdist/generated/${seed}" \
      "/etc/dnsdist/generated/${seed}"
  fi
done

install -m 0644 "${script_dir}"/dnsdist_automation/*.py \
  /usr/local/lib/dnsdist-automation/dnsdist_automation/
install -m 0755 "${script_dir}/update-dnsdist-domains.py" \
  /usr/local/sbin/update-dnsdist-domains.py
install -m 0755 "${script_dir}/update-dnsdist-ecs.py" \
  /usr/local/sbin/update-dnsdist-ecs.py

install -m 0644 \
  "${system_tree}/etc/systemd/system/dnsdist.service.d/10-dnsdist-automation.conf" \
  /etc/systemd/system/dnsdist.service.d/10-dnsdist-automation.conf
install -m 0644 "${system_tree}"/etc/systemd/system/dnsdist-*-update.service \
  "${system_tree}"/etc/systemd/system/dnsdist-*-update.timer \
  /etc/systemd/system/

systemctl daemon-reload

# Build both generated files before dnsdist starts. The first command requires
# network access; the second requires a configured WireGuard interface.
/usr/local/sbin/update-dnsdist-domains.py --no-check --no-reload
/usr/local/sbin/update-dnsdist-ecs.py --no-check --no-reload

dnsdist --check-config -C /etc/dnsdist/dnsdist.conf
systemctl enable dnsdist.service
systemctl restart dnsdist.service
systemctl enable --now \
  dnsdist-domain-update.timer \
  dnsdist-ecs-update.timer

printf '%s\n' 'dnsdist policy gateway installed successfully'
