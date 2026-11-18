# infrastructure-ops

Stuff to make life easier along the way: small, standalone tools from running a homelab.

| Tool | What it does |
|---|---|
| [dell-fan-guard](dell-fan-guard/) | Quieter Dell PowerEdge fans, driven by every temperature including each drive's own SMART limit, and never louder than stock |
| [zabbix/](zabbix/) | Zabbix tools and notes, listed below |

## Zabbix

| Tool | What it does |
|---|---|
| [apt-updates](zabbix/apt-updates/) | Template for pending and security APT updates on Debian, Ubuntu and Proxmox, with nothing installed on the hosts |
| [nginx-cert-discovery](zabbix/nginx-cert-discovery/) | Discovers every TLS site nginx serves and watches each certificate's expiry and validity |
| [alert-hygiene](zabbix/alert-hygiene/) | Notes and a script for fewer, truer alerts |
| [telegram-relay](zabbix/telegram-relay/) | Sends alerts to Telegram with a per-minute cap and a digest for the overflow |

MIT licensed; see [LICENSE](LICENSE).
