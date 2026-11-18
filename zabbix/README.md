# Zabbix tools

Built and run on Zabbix 8.0 with Agent 2 on Debian 13 and Proxmox VE 9.

| Tool | What it does |
|---|---|
| [apt-updates](apt-updates/) | Template for pending and security APT updates on Debian, Ubuntu and Proxmox, with nothing installed on the hosts |
| [nginx-cert-discovery](nginx-cert-discovery/) | Discovers every TLS site nginx serves and watches each certificate's expiry and validity |
| [alert-hygiene](alert-hygiene/) | Notes and a script for fewer, truer alerts |
| [telegram-relay](telegram-relay/) | Sends alerts to Telegram with a per-minute cap and a digest for the overflow |
