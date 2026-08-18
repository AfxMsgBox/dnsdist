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

if [[ ${install_packages} -eq 1 ]]; then
  apt-get update
  apt-get install -y dnsdist wireguard-tools python3
fi

for command in dnsdist wg python3 systemctl install; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf 'required command is missing: %s\n' "${command}" >&2
    exit 1
  fi
done

if [[ -e /etc/dnsdist/dnsdist.yml ]]; then
  printf '%s\n' \
    '/etc/dnsdist/dnsdist.yml exists and may take precedence over the Lua config.' \
    'Move or merge it deliberately before running this installer.' >&2
  exit 1
fi

install -d -m 2750 -o root -g dnsdist /etc/dnsdist/generated
install -d -m 0755 /etc/systemd/system/dnsdist.service.d
install -d -m 0755 /usr/local/lib/dnsdist-automation/dnsdist_automation

install -m 0640 -o root -g dnsdist \
  "${system_tree}/etc/dnsdist/dnsdist.conf" \
  /etc/dnsdist/dnsdist.conf

if [[ ! -e /etc/default/dnsdist-automation ]]; then
  install -m 0644 \
    "${system_tree}/etc/default/dnsdist-automation" \
    /etc/default/dnsdist-automation
fi

for seed in domain-rules.lua ecs-rules.lua; do
  if [[ ! -e "/etc/dnsdist/generated/${seed}" ]]; then
    install -m 0640 -o root -g dnsdist \
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
systemctl enable --now dnsdist.service
systemctl enable --now \
  dnsdist-domain-update.timer \
  dnsdist-ecs-update.timer

printf '%s\n' 'dnsdist policy gateway installed successfully'
