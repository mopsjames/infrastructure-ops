# Zabbix: discover and watch every TLS certificate nginx serves

Add an HTTPS site to nginx and Zabbix starts watching its certificate within the hour: expiry, chain validation and hostname match, with no Zabbix edits. Useful behind a reverse proxy with many names, or with short-lived certificates (ACME, step-ca) where a broken renewal must be caught before it expires.

`nginx-cert-discovery.py` lists every `server_name` in `/etc/nginx/sites-enabled/` whose server block listens with `ssl`, skipping `_`, wildcards and catch-all blocks with `ssl_reject_handshake on`. It prints Zabbix low-level discovery JSON:

```json
[{"{#CERT.HOST}": "api.example.com"}, {"{#CERT.HOST}": "app.example.com"}]
```

Each name is then checked with Zabbix Agent 2's built-in `web.certificate.get`, against `127.0.0.1:443` with that name as SNI, so it sees exactly what nginx serves.

## Install on the nginx host

```sh
install -D -m 755 nginx-cert-discovery.py /etc/zabbix/scripts/nginx-cert-discovery.py
echo 'UserParameter=nginx.cert.discovery,/etc/zabbix/scripts/nginx-cert-discovery.py' \
  > /etc/zabbix/zabbix_agent2.d/nginx-cert.conf
systemctl restart zabbix-agent2
zabbix_agent2 -t nginx.cert.discovery     # should print the JSON
```

Needs Zabbix Agent 2 (`web.certificate.get` isn't in the classic agent) and Python 3.

## Zabbix configuration

On the nginx host (or a template), create a discovery rule:

- **Discovery rule:** key `nginx.cert.discovery`, type Zabbix agent, update interval 1h.
- **Macros** on the host or template: `{$NGINX.CERT.EXPIRY.WARN}` = 7, `{$NGINX.CERT.EXPIRY.HIGH}` = 2 (days). Set WARN a few days below your renewal point, so it means "renewal is failing".

Item prototypes:

| Name | Key | Type | Type of information | Preprocessing |
|---|---|---|---|---|
| TLS {#CERT.HOST}: certificate check | `web.certificate.get[{#CERT.HOST},443,127.0.0.1]` | Zabbix agent, 1h | Text | none (master) |
| TLS {#CERT.HOST}: expires | `nginx.cert.expires[{#CERT.HOST}]` | Dependent | Numeric (unsigned), units `unixtime` | JSONPath `$.x509.not_after.timestamp` |
| TLS {#CERT.HOST}: validation | `nginx.cert.result[{#CERT.HOST}]` | Dependent | Character | JSONPath `$.result.value` |
| TLS {#CERT.HOST}: validation message | `nginx.cert.message[{#CERT.HOST}]` | Dependent | Character | JSONPath `$.result.message` |
| TLS {#CERT.HOST}: subject | `nginx.cert.subject[{#CERT.HOST}]` | Dependent | Character | JSONPath `$.x509.subject` |

Trigger prototypes (replace `HOST` with the host or template name):

| Name | Severity | Expression |
|---|---|---|
| TLS {#CERT.HOST}: certificate expires in under {$NGINX.CERT.EXPIRY.HIGH} days | High | `(last(/HOST/nginx.cert.expires[{#CERT.HOST}])-now())/86400<{$NGINX.CERT.EXPIRY.HIGH}` |
| TLS {#CERT.HOST}: certificate expires in under {$NGINX.CERT.EXPIRY.WARN} days (renewal failing?) | Warning, depends on the High one | `(last(/HOST/nginx.cert.expires[{#CERT.HOST}])-now())/86400<{$NGINX.CERT.EXPIRY.WARN}` |
| TLS {#CERT.HOST}: certificate not valid | Average | `last(/HOST/nginx.cert.result[{#CERT.HOST}])<>"valid"` |
| TLS {#CERT.HOST}: no certificate data for 3 hours | Warning | `nodata(/HOST/nginx.cert.expires[{#CERT.HOST}],3h)=1` |

"Not valid" covers an expired certificate, a broken chain and a name mismatch. With a private CA, the agent's host must trust that CA's root, or every certificate reads as not valid.

## Notes

- Only names in `sites-enabled` are found; a `server_name` defined in `nginx.conf` or `conf.d/` isn't. Change the glob if you use those.
- Ports other than 443 aren't covered; add a second item prototype for those.
- Built and run on Zabbix 8.0 with Agent 2 on Debian 13.
