"""Test Plex API - uses only Python stdlib. Run: python3 test_plex.py"""
import os
import json
import urllib.request
import urllib.error

TOKEN = os.environ.get("PLEX_TOKEN", "")

HEADERS = {
    "X-Plex-Token": TOKEN,
    "X-Plex-Product": "Plex Watch HA",
    "X-Plex-Version": "1.0.0",
    "X-Plex-Client-Identifier": "homeassistant-plex-watch",
    "X-Plex-Platform": "Home Assistant",
    "Accept": "application/json",
}

import ssl
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)

if not TOKEN:
    print("ERROR: PLEX_TOKEN no está definido. Ejecuta: export PLEX_TOKEN=tu_token")
    exit(1)

print(f"Token: {TOKEN[:6]}...{TOKEN[-4:]} (len={len(TOKEN)})")

print("\n--- Validando token (GET /api/v2/user) ---")
status, body = get("https://plex.tv/api/v2/user")
print(f"  HTTP {status}")
if status == 200:
    u = json.loads(body)
    print(f"  Usuario: {u.get('username') or u.get('email')}")
else:
    print(f"  Body: {body[:300]}")

print("\n--- Recursos (GET /api/v2/resources) ---")
status, body = get("https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1")
print(f"  HTTP {status}")
if status == 200:
    data = json.loads(body)
    print(f"  Dispositivos totales: {len(data)}")
    for d in data:
        conns = d.get("connections", [])
        tok = d.get("accessToken")
        tok_display = f"{tok[:6]}...{tok[-4:]}" if tok and len(tok) > 10 else repr(tok)
        print(f"  - name={d.get('name')!r} provides={d.get('provides')!r} owned={d.get('owned')} presence={d.get('presence')} connections={len(conns)}")
        print(f"      accessToken={tok_display}")
        for c in conns:
            print(f"      uri={c.get('uri')} local={c.get('local')} relay={c.get('relay')} protocol={c.get('protocol')}")
else:
    print(f"  Body: {body[:500]}")

