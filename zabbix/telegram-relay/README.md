# Zabbix to Telegram relay, rate-limited

Zabbix will fire 40 alerts in a minute; Telegram won't deliver them, and a phone that buzzes 40 times gets muted. This small relay sits between them: it sends **at most 5 messages a minute** and folds the overflow into **one digest message**, so a storm still reaches you at once, in one message.

- **Cap:** at most `TG_MAX_PER_MINUTE` messages (default 5) in any rolling 60 seconds, 1.1 s apart.
- **Digest:** when more are waiting than fit, the last allowed message lists the rest (up to 25 lines, then "...and N more").
- **Flaps cost nothing:** a problem that recovers before it's sent is dropped and only counted in the next digest.
- **429 handling:** on a send error it waits Telegram's `retry_after` (at least 15 s) and tries again; the queue is kept.
- **Nothing exposed:** it listens on `127.0.0.1:8377` only. Python 3 standard library, one file.

Not included: Ack or Silence buttons, editing messages on recovery, or persisting the queue across restarts. A queued alert is lost if the relay restarts.

## Telegram's limits

From Telegram's [Bot FAQ](https://core.telegram.org/bots/faq): about 1 message a second in one chat, 20 a minute in a group, about 30 a second across all chats. Over that, the API answers HTTP 429 with `retry_after`.

## Fix the volume in Zabbix first

Most floods are a few noisy triggers, not an outage:

1. Send only Average and above (an action condition on trigger severity).
2. Demote triggers that aren't actionable; for example, set `Linux: Load average is too high` to Information. Alert on the service check that fails when a busy host stops serving.
3. Give flaky checks tolerance: several failures over a few minutes, not one failed poll.
4. Turn off update operations on the action, or every acknowledgement sends a message too.

## Setup

1. Create a bot with @BotFather. Put its token in `/etc/zabbix-telegram-relay/bot-token` (owner root, mode 600).
2. Message the bot, then read your chat ID from `https://api.telegram.org/bot<token>/getUpdates` (`message.chat.id`).
3. Install the relay on the Zabbix server:
   ```sh
   install -m 755 relay.py /usr/local/sbin/zabbix-telegram-relay
   install -m 644 zabbix-telegram-relay.service /etc/systemd/system/
   # set TG_CHAT in the unit, then:
   systemctl daemon-reload && systemctl enable --now zabbix-telegram-relay
   ```
   `LoadCredential` with `%d` needs systemd 250 or newer. On older systems, drop those two lines and `DynamicUser`, and set `TG_TOKEN_FILE` to a file the service user can read.
4. In Zabbix (Alerts → Media types), create a **Webhook** media type with the script in `webhook.js` and these parameters:

   | Parameter | Value |
   | --- | --- |
   | `url` | `http://127.0.0.1:8377/alert` |
   | `eventid` | `{EVENT.ID}` |
   | `value` | `{EVENT.VALUE}` |
   | `update` | `{EVENT.UPDATE.STATUS}` |
   | `host` | `{HOST.NAME}` |
   | `name` | `{EVENT.NAME}` |

   Give a user that media (any "send to" value) and add a trigger action: severity at least Average, with operations for problems and recoveries, no update operations.
5. Test with a throwaway trapper item and trigger, then delete them.
6. Monitor the relay itself. If it, the Zabbix server or the internet is down, nothing reaches Telegram, so keep a second channel or watch the dashboard. Give that check tolerance: alert on several minutes of failure, such as `max(/host/key,10m)=0 or nodata(/host/key,10m)=1`, not on `last()=0`, or every short Telegram or DNS blip pages you about a relay that catches up on its own.

## Tested

The cap, digest, flap and 429 logic were tested against a fake Telegram (12 alerts with one flap became 4 messages and a digest of 7; a 429 waited 30 s), and the HTTP endpoint against curl. Not yet run against a real bot or a real Zabbix in this cut-down form; it's derived from a longer relay that has run in a homelab since November 2026.

## Lessons from running it

- **Hourly DNS-down alerts** turned out to be network discovery flooding the DNS server with reverse lookups until its per-subnet rate limit dropped everyone's queries, including the relay's lookup of `api.telegram.org`. Whitelisting the Zabbix server and running discovery less often stopped the DNS alerts, but the relay still misses a few lookups during each discovery run.
- **Every resolve showed up twice** while recoveries were sent both as an edit and as a reply. Pick one.
- **Telegram's API has its own blips** (502s and timeouts for 1 to 5 minutes, several times a week, while the internet link is fine). The relay holds its queue and catches up. A health check that alerted on one failed poll flapped 9 times in 4 days from these blips and the discovery DNS misses; requiring 10 minutes of failure would have silenced all of them.
