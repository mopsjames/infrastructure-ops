# Zabbix template: pending APT updates

Know which Debian, Ubuntu or Proxmox hosts have updates waiting, and get warned when **security** updates have sat for a week, with **nothing installed on the hosts**. Zabbix Agent 2 runs a simulated upgrade itself once an hour.

## What it creates

A template, `Linux APT updates`, built through the Zabbix API by `apt-updates-template.sh`:

| Item | Key | Notes |
|---|---|---|
| APT: simulated upgrade | `system.run["apt-get -s -o Debug::NoLocking=1 dist-upgrade ... "]` | Master item, hourly, not stored. `-s` only simulates; `NoLocking` lets it run while another apt holds the lock |
| APT: updates pending | `apt.updates.total` | Dependent: number of packages a full upgrade would install |
| APT: security updates pending | `apt.updates.security` | Dependent: those coming from a security repository |
| APT: pending packages | `apt.updates.packages` | Dependent: "name new-version repo" per line, to read in the problem view |
| APT: package lists last refreshed | `vfs.file.time[/var/cache/apt/pkgcache.bin,modify]` | When apt last refreshed its lists |

| Trigger | Severity | Fires when |
|---|---|---|
| Security updates pending for 7 days | Warning | Every hourly value for a week is above 0 (and most of a week of data exists, so it can't fire on day one) |
| APT update check not reporting | Warning | No data for 3 hours: agent down, apt broken, or the item failing |
| APT package lists not refreshed for 3 days | Information | The counts can't be trusted |

The check runs as the `zabbix` user, so it can't refresh package lists itself. Each host must do that: `unattended-upgrades` (or a daily `apt-get update` timer) on Debian and Ubuntu; Proxmox's own daily `pveupdate` does it on PVE hosts. The Information trigger tells you when one doesn't.

## Install

1. On each host, allow the check in the agent (Zabbix Agent 2), then restart it:
   ```sh
   echo 'AllowKey=system.run[*]' > /etc/zabbix/zabbix_agent2.d/system-run.conf
   systemctl restart zabbix-agent2
   ```
   That lets the server run any command as the `zabbix` user. A narrower `AllowKey` pattern for just this key should work but hasn't been tested.
2. Create an API token in Zabbix (Users → API tokens) for a user that can edit templates and hosts.
3. Run the script anywhere with `curl` and `jq`, naming the hosts to link:
   ```sh
   ZABBIX_URL=https://zabbix.example.com ZABBIX_TOKEN_FILE=~/.zabbix-token \
     bash apt-updates-template.sh web1 db1 pve1
   ```
   Re-run it any time to link more hosts; existing items, triggers and history are left alone.

## Notes

- The simulation takes 2–3 seconds, more than the agent's default 3-second timeout allows comfortably, so the item's timeout is 60 s.
- When nothing is pending, the command still prints an `END` line and exits 0, so the item doesn't go unsupported on an up-to-date host.
- "Security" means the repository name (inside the parentheses of each `Inst` line) contains "security", which matches Debian's and Ubuntu's security repositories.
- Built and run on Zabbix 8.0 with Debian 13 and Proxmox VE 9 hosts.
