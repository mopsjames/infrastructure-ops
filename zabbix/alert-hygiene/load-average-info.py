#!/usr/bin/env python3
"""Make "Load average is too high" informational instead of an alert.

A host with high load that's still serving isn't an emergency. If load takes a service or
host down, its service check or agent-availability trigger alerts instead.

Usage: ZABBIX_URL=https://zabbix.example.com ZABBIX_TOKEN_FILE=~/.zabbix-token python3 load-average-info.py
       (or ZABBIX_TOKEN=... instead of ZABBIX_TOKEN_FILE). Safe to re-run.

Sets the template trigger "Linux: Load average is too high" in "Linux by Zabbix agent",
"Linux by Zabbix agent active" and "Linux by SNMP" to Information. Every host's copy
inherits the change. Importing a newer version of those templates resets it: re-run then.
"""
import os
import json
import urllib.request

API = os.environ["ZABBIX_URL"].rstrip("/") + "/api_jsonrpc.php"
TOKEN = os.environ.get("ZABBIX_TOKEN") or open(os.path.expanduser(os.environ["ZABBIX_TOKEN_FILE"])).read().strip()
TEMPLATES = ["Linux by Zabbix agent", "Linux by Zabbix agent active", "Linux by SNMP"]
NAME = "Linux: Load average is too high"
INFORMATION = "1"


def api(method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(API, body, {"Content-Type": "application/json-rpc",
                                             "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if "error" in out:
        raise SystemExit(f"{method}: {out['error']}")
    return out["result"]


for tpl in TEMPLATES:
    res = api("template.get", {"filter": {"host": [tpl]}, "output": ["templateid"]})
    if not res:
        print(f"{tpl}: template not found, skipped")
        continue
    trig = api("trigger.get", {"templateids": res[0]["templateid"], "filter": {"description": [NAME]},
                               "output": ["triggerid", "priority"]})
    if not trig:
        print(f"{tpl}: trigger not found, skipped")
        continue
    t = trig[0]
    if t["priority"] == INFORMATION:
        print(f"{tpl}: already Information")
        continue
    api("trigger.update", {"triggerid": t["triggerid"], "priority": INFORMATION})
    print(f"{tpl}: priority {t['priority']} -> Information")

left = api("trigger.get", {"filter": {"description": [NAME]}, "inherited": True,
                           "output": ["priority"], "selectHosts": ["host"]})
print("host triggers not yet Information:",
      [t["hosts"][0]["host"] for t in left if t["priority"] != INFORMATION] or "none")
