#!/bin/bash
# Creates the "Linux APT updates" Zabbix template and links it to hosts. Safe to re-run:
# anything that already exists is left alone, so history is never lost.
# Usage: ZABBIX_URL=https://zabbix.example.com ZABBIX_TOKEN_FILE=~/.zabbix-token bash apt-updates-template.sh [host ...]
#   (or ZABBIX_TOKEN=... instead of ZABBIX_TOKEN_FILE). Hosts are Zabbix technical names,
#   on Debian, Ubuntu or Proxmox with Zabbix Agent 2. Needs curl and jq.
#   The API token's user needs write access to templates and the hosts named.
#
# Nothing is installed on the hosts. The agent (user zabbix, no root needed) runs the
# check itself through system.run, which must be allowed in the agent config, e.g.
#   AllowKey=system.run[*]   in a file under /etc/zabbix/zabbix_agent2.d/
# That allows any command the server sends. A narrower AllowKey pattern for just this
# key should work but hasn't been tested.
#
#   apt.updates.raw  (master, hourly)  apt-get -s dist-upgrade, keeping the "Inst" lines.
#       -s only simulates: it lists what a full upgrade would install, installs nothing.
#       NoLocking lets it run while another apt holds the lock. Each line looks like
#       Inst libssl3t64 [3.5.1-1] (3.5.1-1+deb13u1 Debian-Security:13/stable-security [amd64])
#       Not stored; the three items below are computed from it, so apt runs once an hour.
#   apt.updates.total     dependent, number of Inst lines
#   apt.updates.security  dependent, Inst lines whose repo (inside the parentheses) is a security one
#   apt.updates.packages  dependent, text "name new-version repo", for the problem view
#   vfs.file.time[/var/cache/apt/pkgcache.bin,modify]  when apt last rebuilt its package
#       lists. The check only sees updates apt knows about, and refreshing lists needs root,
#       so hosts without unattended-upgrades (or Proxmox's daily pveupdate) go stale.
#
# Triggers:
#   Warning  security updates pending for 7 days (every hourly value that week above 0)
#   Warning  the check stopped reporting for 3 hours (agent down, apt broken, item failing)
#   Info     package lists not refreshed for 3 days, so the counts can't be trusted
set -euo pipefail
command -v curl >/dev/null && command -v jq >/dev/null || { echo "needs curl and jq" >&2; exit 1; }

API=${ZABBIX_URL:?set ZABBIX_URL, e.g. https://zabbix.example.com}/api_jsonrpc.php
if [ -z "${ZABBIX_TOKEN:-}" ]; then
  [ -s "${ZABBIX_TOKEN_FILE:-}" ] || { echo "Set ZABBIX_TOKEN or ZABBIX_TOKEN_FILE" >&2; exit 1; }
  ZABBIX_TOKEN=$(<"$ZABBIX_TOKEN_FILE")
fi
TOKEN=$ZABBIX_TOKEN

TPL_NAME="Linux APT updates"
LISTS_KEY="vfs.file.time[/var/cache/apt/pkgcache.bin,modify]"
# "|| true" and the END marker keep the output non-empty and the exit code 0 when
# nothing is pending, so the item never goes unsupported just because a host is current.
RAW_KEY='system.run["apt-get -s -o Debug::NoLocking=1 dist-upgrade 2>/dev/null | grep ^Inst || true; echo END"]'

api() {
  local out
  out=$(curl -sf -X POST -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $TOKEN" \
    -d "$(jq -nc --arg m "$1" --argjson p "$2" '{jsonrpc:"2.0",method:$m,params:$p,id:1}')" "$API")
  if jq -e '.error' <<<"$out" >/dev/null; then
    echo "$1 failed: $(jq -c .error <<<"$out")" >&2
    exit 1
  fi
  jq -c .result <<<"$out"
}

# Template, in the stock "Templates" group.
TPL_ID=$(api template.get "$(jq -nc --arg n "$TPL_NAME" '{output:["templateid"],filter:{host:$n}}')" | jq -r '.[0].templateid // empty')
if [ -z "$TPL_ID" ]; then
  GROUP_ID=$(api templategroup.get '{"output":["groupid"],"filter":{"name":"Templates"}}' | jq -r '.[0].groupid')
  TPL_ID=$(api template.create "$(jq -nc --arg n "$TPL_NAME" --arg g "$GROUP_ID" '{
    host:$n, groups:[{groupid:$g}],
    description:"Pending apt updates, checked by the agent with system.run. Source: https://github.com/mopsjames/infrastructure-ops"}')" \
    | jq -r '.templateids[0]')
  echo "Created template $TPL_NAME ($TPL_ID)"
fi

item_id() {
  api item.get "$(jq -nc --arg t "$TPL_ID" --arg k "$1" '{output:["itemid"],templateids:$t,filter:{key_:$k}}')" | jq -r '.[0].itemid // empty'
}

# Creates the item from a JSON object unless its key already exists; prints its id.
item() {
  local key id
  key=$(jq -r .key_ <<<"$1")
  id=$(item_id "$key")
  if [ -z "$id" ]; then
    id=$(api item.create "$(jq -c --arg t "$TPL_ID" '. + {hostid:$t, tags:[{tag:"component",value:"updates"}]}' <<<"$1")" | jq -r '.itemids[0]')
    echo "Created item: $(jq -r .name <<<"$1")" >&2
  fi
  echo "$id"
}

# Master item: Zabbix agent (type 0), text (value_type 4), history 0 = not stored.
# The simulation takes 2-3 s, longer than the default 3 s agent timeout allows for.
RAW_ID=$(item "$(jq -nc --arg k "$RAW_KEY" '{name:"APT: simulated upgrade", key_:$k, type:0,
  value_type:4, delay:"1h", timeout:"60s", history:"0"}')")

# JavaScript preprocessing (type 21). Each step gets the master's text as "value".
JS_LINES='var l = value.split("\n").filter(function (s) { return s.indexOf("Inst ") === 0; });'
JS_TOTAL="$JS_LINES return l.length;"
JS_SECURITY="$JS_LINES return l.filter(function (s) {
  var p = s.indexOf(\"(\"); return p >= 0 && s.slice(p).toLowerCase().indexOf(\"security\") >= 0; }).length;"
JS_PACKAGES="$JS_LINES return l.map(function (s) {
  var m = s.match(/^Inst (\\S+) .*?\\((\\S+) (\\S+)/); return m ? m[1] + \" \" + m[2] + \" \" + m[3] : s;
}).join(\"\\n\") || \"none\";"

# Dependent items (type 18) on the master.
dep() {  # dep <name> <key> <value_type> <history> <javascript>
  item "$(jq -nc --arg n "$1" --arg k "$2" --argjson v "$3" --arg h "$4" --arg js "$5" --arg m "$RAW_ID" '{
    name:$n, key_:$k, type:18, master_itemid:$m, value_type:$v, history:$h,
    preprocessing:[{type:21, params:$js, error_handler:0, error_handler_params:""}]}')" >/dev/null
}
dep "APT: updates pending"          apt.updates.total    3 90d "$JS_TOTAL"
dep "APT: security updates pending" apt.updates.security 3 90d "$JS_SECURITY"
dep "APT: pending packages"         apt.updates.packages 4 7d  "$JS_PACKAGES"

item "$(jq -nc --arg k "$LISTS_KEY" '{name:"APT: package lists last refreshed", key_:$k, type:0,
  value_type:3, units:"unixtime", delay:"1h", history:"30d"}')" >/dev/null

# trigger <name> <severity> <expression>   (severity 1 = Information, 2 = Warning)
trigger() {
  local exists
  exists=$(api trigger.get "$(jq -nc --arg t "$TPL_ID" --arg n "$1" '{output:["triggerid"],templateids:$t,filter:{description:$n}}')" | jq length)
  if [ "$exists" = 0 ]; then
    api trigger.create "$(jq -nc --arg n "$1" --argjson s "$2" --arg e "$3" '{
      description:$n, priority:$s, expression:$e, manual_close:1,
      comments:"The list is in the item APT: pending packages. Patch guests with apt full-upgrade; Proxmox hosts by hand, one at a time.",
      tags:[{tag:"scope",value:"security"}]}')" >/dev/null
    echo "Created trigger: $1"
  fi
}
T="/$TPL_NAME"
# min over 7d alone would fire on day one, when only an hour of history exists, so it
# also needs most of a week of hourly values (160 of 168).
trigger "Security updates pending for 7 days on {HOST.NAME} ({ITEM.LASTVALUE1})" 2 \
  "min($T/apt.updates.security,7d)>0 and count($T/apt.updates.security,7d)>=160"
trigger "APT update check not reporting on {HOST.NAME}" 2 \
  "nodata($T/apt.updates.total,3h)=1"
trigger "APT package lists not refreshed for 3 days on {HOST.NAME}" 1 \
  "fuzzytime($T/$LISTS_KEY,259200)=0"

# Link the template to the hosts named on the command line.
for name in "$@"; do
  HOST_ID=$(api host.get "$(jq -nc --arg n "$name" '{output:["hostid"],filter:{host:$n}}')" | jq -r '.[0].hostid // empty')
  [ -n "$HOST_ID" ] || { echo "No Zabbix host named $name, skipped" >&2; continue; }
  api host.massadd "$(jq -nc --arg h "$HOST_ID" --arg t "$TPL_ID" '{hosts:[{hostid:$h}],templates:[{templateid:$t}]}')" >/dev/null
  echo "Linked $TPL_NAME to $name"
done
