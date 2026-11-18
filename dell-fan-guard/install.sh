#!/bin/bash
# Installs fan-guard on a Dell PowerEdge running Debian or Proxmox. Safe to re-run.
# Usage, as root, from this folder: bash install.sh [--no-start]
set -euo pipefail
cd "$(dirname "$0")"
command -v ipmitool >/dev/null && command -v smartctl >/dev/null || {
  echo "needs ipmitool and smartctl: apt install ipmitool smartmontools" >&2; exit 1; }

install -m 0755 fan-guard.py /usr/local/sbin/fan-guard

cat > /etc/systemd/system/fan-guard.service <<'UNIT'
[Unit]
Description=fan-guard: turn Dell fans down where temperatures allow
After=multi-user.target

[Service]
ExecStart=/usr/local/sbin/fan-guard
# Whatever happens to the script, the iDRAC gets fan control back.
ExecStopPost=/usr/bin/ipmitool raw 0x30 0x30 0x01 0x01
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT

# Small reader for monitoring: fan-guard-state mode|pct|age|reason
# (pct is -1 on automatic; age is seconds since the last loop, large = stuck or stopped).
cat > /usr/local/sbin/fan-guard-state <<'HELPER'
#!/usr/bin/python3
import json, sys, time
try:
    s = json.load(open('/run/fan-guard/state.json'))
except OSError:
    s = {'mode': 'stopped', 'pct': None, 'ts': 0}
f = sys.argv[1]
print(int(time.time()) - s['ts'] if f == 'age' else (-1 if s.get('pct') is None else s['pct']) if f == 'pct' else s.get(f, ''))
HELPER
chmod 0755 /usr/local/sbin/fan-guard-state

# Zabbix agent 2, if present: fan-guard.mode, .pct, .age, .reason
if [ -d /etc/zabbix/zabbix_agent2.d ]; then
  cat > /etc/zabbix/zabbix_agent2.d/fan-guard.conf <<'CONF'
UserParameter=fan-guard.mode,/usr/local/sbin/fan-guard-state mode
UserParameter=fan-guard.pct,/usr/local/sbin/fan-guard-state pct
UserParameter=fan-guard.age,/usr/local/sbin/fan-guard-state age
UserParameter=fan-guard.reason,/usr/local/sbin/fan-guard-state reason
CONF
  systemctl restart zabbix-agent2
fi

systemctl daemon-reload
if [ "${1:-}" = --no-start ]; then
  echo "installed, not started. Check readings first: fan-guard --status"
else
  systemctl enable --now fan-guard
  systemctl --no-pager --lines=5 status fan-guard
fi
