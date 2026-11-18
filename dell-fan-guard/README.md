# fan-guard for Dell PowerEdge

Dell's 13th-generation servers (R530, R630, R730, R730xd and friends) run their fans far faster than a homelab needs, especially with non-Dell cards or drives installed. fan-guard takes manual control through IPMI and turns the fans down **only as far as every temperature allows**, and gives control back to the iDRAC the moment anything looks wrong.

> **Warning.** Manual fan control can overheat hardware. fan-guard is built to fail safe, but you run it at your own risk. Watch `fan-guard --status` and your drive temperatures for the first day, especially drives in rear or internal bays.

## How it decides

Every minute it reads the inlet and CPU temperatures (IPMI), every drive (smartctl) and an Nvidia GPU if there is one. Each sensor has a **target** and a **hard limit**.

- **Drives use their own limits.** Most SAS drives report a SMART "Drive Trip Temperature", which differs by model (50, 60 and 64 C on the drives it was tested with). A drive's target is its trip minus 9 C (minus 7 for SSDs) and its hard limit is the trip minus 4. Drives without a trip temperature use fixed fallbacks.
- **Down slowly:** while every sensor is at least 3 C under target, it lowers the fans by 1% every 5 minutes, never below `min_pct`. Drives heat up minutes after the fans slow, so stepping down fast overshoots.
- **Up fast:** when anything goes over target, it raises the fans 6% a minute.
- **Never louder than stock:** before taking control it records the RPM automatic mode was running. If cooling would need more than that, it hands back to automatic.
- **Fail safe:** a sensor it can't read, a missing reading, or any hard limit reached hands control back to the iDRAC at once, for 30 minutes. If the script stops or dies, systemd's `ExecStopPost` sets automatic mode too.

## Install

Debian or Proxmox, as root, with the iDRAC's IPMI reachable from the host (`ipmitool sdr` works):

```sh
apt install ipmitool smartmontools
bash install.sh --no-start
fan-guard --status          # check every sensor is read and the limits look right
systemctl enable --now fan-guard
journalctl -u fan-guard -f  # each change is logged
```

## Settings

Optional: copy `fan-guard.example.json` to `/etc/fan-guard.json` and change what you need, then `systemctl restart fan-guard`. Anything left out keeps its default; `limits` can be given per sensor kind.

| Setting | Default | What it does |
|---|---|---|
| `min_pct` | 15 | Lowest fan speed fan-guard will set |
| `start_pct` | 30 | First manual setting |
| `watch_only` | false | Only read and report; never take the fans |
| `step_down`, `step_down_every` | 1, 5 | Lower by 1% every 5 intervals when there is room |
| `step_up` | 6 | Raise by 6% per interval when over target |
| `step_down_margin` | 3 | Degrees under target needed before stepping down |
| `trip_target_gap`, `trip_hard_gap` | hdd 9, ssd 7; 4 | Targets and hard limits from each drive's trip temperature |
| `limits` | inlet 27/32, cpu 65/80, hdd 45/50, ssd 55/60, gpu 75/85 | Target and hard limit in C, per sensor kind |
| `interval`, `retry_after` | 60, 1800 | Seconds between readings; seconds on automatic after a hand-back |

## Monitoring

`install.sh` adds a small reader, `fan-guard-state mode|pct|age|reason`, and, if Zabbix Agent 2 is installed, the keys `fan-guard.mode`, `fan-guard.pct` (-1 on automatic), `fan-guard.age` (seconds since the last loop) and `fan-guard.reason`. Useful triggers:

- **fan-guard is not running:** `age` over 300, or no data for 10 minutes.
- **fan-guard handed fans to the iDRAC:** `reason` is not empty. Automatic with an empty reason just means there's no room to turn down.

Also set drive-temperature alerts **below each model's trip temperature**. Generic thresholds such as 55/60 C sit at or above the trip on some 8 TB SAS drives.

## Lessons from running it

- **The rear flex bay of an R730xd is a hot spot.** An SSD there went from 52 to 59 C within minutes of the fans dropping to 16%, and to 58 C even at a slow step-down from 30%. A 40% floor (`min_pct`) held it at 53–54 C, still far quieter than stock (about 10,500 RPM). Better still: move hot drives to front bays.
- **Automatic mode can spin up for no visible reason**, such as a third-party PCIe card, which is when fan-guard saves the most noise.
- **`--status` before enabling** catches sensors that don't read on your model.

## Tested

Two Dell servers (R530 and R730xd) on Proxmox VE 9 with SAS hard drives, SAS SSDs and an Nvidia Quadro. Newer generations (14th, such as the R740) need different raw commands, and later iDRAC 9 firmware reportedly blocks manual fan control. Not tested there.
