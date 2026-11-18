#!/usr/bin/python3
"""Lists every server_name nginx serves over TLS, for Zabbix LLD (web.certificate.get)."""
import glob, json, re
names = set()
for path in glob.glob('/etc/nginx/sites-enabled/*'):
    try:
        text = open(path).read()
    except OSError:
        continue
    text = re.sub(r'#.*', '', text)
    for block in re.split(r'\bserver\s*\{', text)[1:]:
        if not re.search(r'listen[^;]*\bssl\b', block) or 'ssl_reject_handshake on' in block:
            continue
        for m in re.finditer(r'(?<![\w])server_name\s+([^;]+);', block):
            names.update(n for n in m.group(1).split() if n != '_' and '*' not in n)
print(json.dumps([{'{#CERT.HOST}': n} for n in sorted(names)]))
