#!/usr/bin/env python3
"""Minimal Zabbix -> Telegram relay with a per-minute cap and a digest for the overflow.

Run: TG_TOKEN=... TG_CHAT=... python3 relay.py   (listens on 127.0.0.1:8377)
     or TG_TOKEN_FILE=/path/to/token instead of TG_TOKEN (the systemd unit does this)
Zabbix: a webhook media type that POSTs the event as JSON to http://127.0.0.1:8377/alert
See README.md. Python 3 standard library only.
"""
import collections, html, http.server, json, os, threading, time, urllib.error, urllib.request

TOKEN = os.environ.get('TG_TOKEN') or open(os.environ['TG_TOKEN_FILE']).read().strip()
CHAT = os.environ['TG_CHAT']
MAX_PER_MINUTE = int(os.environ.get('TG_MAX_PER_MINUTE', 5))
WINDOW, SEND_GAP, DIGEST_LINES = 60, 1.1, 25

lock = threading.Lock()
wake = threading.Event()
pending = collections.OrderedDict()   # key -> alert dict, in arrival order
sent = collections.deque()            # send times inside the window
flapped = 0                           # problems that recovered before being sent
retry_at = 0.0


class SendError(Exception):
    def __init__(self, msg, retry_after=0):
        super().__init__(msg)
        self.retry_after = retry_after


def send(text):
    body = json.dumps({'chat_id': CHAT, 'text': text, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/sendMessage', body,
                                 {'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        try:
            out = json.load(e)
        except ValueError:
            out = {}
        raise SendError(out.get('description') or str(e.code),
                        (out.get('parameters') or {}).get('retry_after', 0))
    except OSError as e:   # never print the request: its URL holds the token
        raise SendError(type(e).__name__)
    time.sleep(SEND_GAP)


def line(a):
    icon = '✅' if str(a['value']) == '0' else '\U0001F534'
    return f"{icon} <b>{html.escape(a['host'])}</b>: {html.escape(a['name'])}"


def on_alert(a):
    """Queue one webhook call. A recovery of a problem not yet sent cancels both."""
    global flapped
    eid, is_problem = str(a['eventid']), str(a['value']) == '1'
    with lock:
        if is_problem:
            pending[eid] = a
        elif eid in pending:
            del pending[eid]
            flapped += 1
        else:
            pending[eid + 'r'] = a
    wake.set()


def pump():
    """Send what the cap allows; if more are waiting, the last allowed message is a digest."""
    global flapped, retry_at
    now = time.time()
    with lock:
        while sent and sent[0] <= now - WINDOW:
            sent.popleft()
        room = MAX_PER_MINUTE - len(sent)
        if now < retry_at or room <= 0 or not pending:
            return
        batch = list(pending.items())
    alone = batch if len(batch) <= room else batch[:room - 1]
    rest = batch[len(alone):]
    try:
        for key, a in alone:
            send(line(a))
            with lock:
                sent.append(time.time())
                pending.pop(key, None)
        if rest:
            with lock:
                gone, flapped = flapped, 0
            lines = [line(a) for _, a in rest]
            text = '\n'.join([f'<b>{len(lines)} more alerts</b> (limit {MAX_PER_MINUTE} a minute)']
                             + lines[:DIGEST_LINES]
                             + ([f'...and {len(lines) - DIGEST_LINES} more'] if len(lines) > DIGEST_LINES else [])
                             + ([f'{gone} came and went before they were shown'] if gone else []))
            try:
                send(text)
            except SendError:
                with lock:
                    flapped += gone
                raise
            with lock:
                sent.append(time.time())
                for key, _ in rest:
                    pending.pop(key, None)
    except SendError as e:
        print('send failed, will retry:', e, flush=True)
        retry_at = time.time() + max(e.retry_after, 15)


def sender():
    while True:
        wake.wait(1)
        wake.clear()
        pump()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/alert':
            return self.send_error(404)
        try:
            a = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if str(a.get('update')) != '1':      # skip acknowledgement updates
                on_alert(a)
        except (ValueError, KeyError, TypeError):
            return self.send_error(400)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    threading.Thread(target=sender, daemon=True).start()
    http.server.ThreadingHTTPServer(('127.0.0.1', 8377), Handler).serve_forever()
