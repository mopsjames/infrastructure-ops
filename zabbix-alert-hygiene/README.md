# Zabbix alert hygiene: fewer, truer alerts

Notes from tuning a homelab Zabbix 8.0 so that a phone notification means something. Most alert floods come from a few noisy triggers, not real outages. Each fix below came from a real false alarm.

## Alert on outcomes, not symptoms

- **Load average is informational.** A host with high load that's still serving isn't an emergency; the service check that fails when it stops serving is. `load-average-info.py` sets the stock `Linux: Load average is too high` trigger to Information on the Linux templates, so every host inherits it. Re-run it after importing newer templates.
- **Containers report the host's load average.** On LXC containers, also raise `{$LOAD_AVG_PER_CPU.MAX.WARN}` (we use 10), or every container alerts whenever the host is busy.
- **Every service gets a check that fails when the service is down**, such as an HTTP or TCP check on its port. An agent on the host proves the host is up, not the service.

## Give flaky checks tolerance

- A check that fails one minute in sixty should need several failures before it alerts. For DNS checks: 2 attempts per poll (`net.dns[<server>,<name>,A,2,2]`, item timeout 10 s) and a trigger of `max(...,5m)=0`, not a single failed poll.
- Health checks of things outside your control (a cloud API, Telegram's servers) should wait a few minutes; their blips aren't yours to fix.

## Find the real cause of "random" failures

- **Hourly DNS-down alerts at minutes 0–6** were Zabbix's own network discovery. Scanning about 2,300 addresses made about 480 reverse-DNS (PTR) lookups a minute to the local DNS server (AdGuard Home), whose per-/24 rate limit (`ratelimit: 20`) then dropped every client in that subnet, including the agent's DNS checks and the alert relay's lookup of its own API. Fixes: add the Zabbix server to the DNS server's rate-limit whitelist, run discovery every 6 hours, not every hour, and add the tolerance above. Dropped queries don't appear in AdGuard's query log, which is why it looked random.
- **The same alert on several hosts in the same minute** is one event wearing many names (a switch or access point rebooting, a hypervisor busy). Look for the shared cause before tuning each host.

## Keep the channel quiet

- Send only **Average and above** to your phone; Warning and Information stay on dashboards.
- Turn off **update operations** on the action, or every acknowledgement sends a message.
- A resolve should edit or answer the original message once, not arrive twice.
- Cap the channel: see [zabbix-telegram-relay](../zabbix-telegram-relay/) for a rate-limited Telegram relay.

## Drive temperatures

Set drive temperature alerts **below each model's own trip temperature**, which SAS drives report through SMART ("Drive Trip Temperature": 50 C on some 8 TB HGST drives, 60 C on some Seagates, 64 C on some Toshiba SSDs). Generic thresholds such as 55/60 C sit at or above the trip on some drives. With Zabbix's SMART template, set `{$SMART.TEMPERATURE.MAX.WARN}` and `{$SMART.TEMPERATURE.MAX.CRIT}` per host; for a custom SAS check, user macros with context let each model have its own limits.
