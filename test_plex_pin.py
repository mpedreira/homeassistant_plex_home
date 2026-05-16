"""
Test completo del flujo PIN de Plex.
Ejecutar con: python3 test_plex_pin.py
No requiere librerías externas.
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "homeassistant-plex-watch-v1"
PLEX_TV = "https://plex.tv"

HEADERS = {
    "X-Plex-Product": "Plex Watch HA",
    "X-Plex-Version": "1.0.0",
    "X-Plex-Client-Identifier": CLIENT_ID,
    "X-Plex-Platform": "Home Assistant",
    "Accept": "application/json",
}

# SSL sin verificación para entornos con certs desactualizados
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def request(method, url, data=None, extra_headers=None):
    all_headers = {**HEADERS, **(extra_headers or {})}
    body = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, data=body, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


# ── PASO 1: Crear PIN ──────────────────────────────────────────────────────────
print("=" * 60)
print("PASO 1: Creando PIN en plex.tv...")
status, body = request(
    "POST",
    f"{PLEX_TV}/api/v2/pins",
    data="strong=true",
    extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
)
print(f"  HTTP {status}")
if status not in (200, 201):
    print(f"  ERROR: {body[:400]}")
    exit(1)

pin_data = json.loads(body)
pin_id = pin_data["id"]
pin_code = pin_data["code"]
print(f"  PIN ID:   {pin_id}")
print(f"  PIN Code: {pin_code}")

# ── PASO 2: URL de autenticación ───────────────────────────────────────────────
params = urllib.parse.urlencode({
    "clientID": CLIENT_ID,
    "code": pin_code,
    "context[device][product]": "Plex Watch HA",
})
auth_url = f"https://app.plex.tv/auth#?{params}"
print()
print("=" * 60)
print("PASO 2: Abre esta URL en tu navegador y autoriza:")
print()
print(f"  {auth_url}")
print()
input("  >> Pulsa ENTER cuando hayas autorizado en el navegador...")

# ── PASO 3: Comprobar si el PIN fue reclamado ──────────────────────────────────
print()
print("=" * 60)
print("PASO 3: Comprobando si el PIN fue autorizado...")
status, body = request("GET", f"{PLEX_TV}/api/v2/pins/{pin_id}")
print(f"  HTTP {status}")
if status != 200:
    print(f"  ERROR: {body[:400]}")
    exit(1)

pin_result = json.loads(body)
token = pin_result.get("authToken")
if not token:
    print("  El PIN NO ha sido reclamado todavía.")
    print("  Asegúrate de autorizar en el navegador antes de pulsar ENTER.")
    # Retry once
    print("  Reintentando en 3 segundos...")
    time.sleep(3)
    status, body = request("GET", f"{PLEX_TV}/api/v2/pins/{pin_id}")
    pin_result = json.loads(body) if status == 200 else {}
    token = pin_result.get("authToken")

if not token:
    print("  ERROR: No se obtuvo token. Flujo de PIN fallido.")
    exit(1)

print(f"  Token obtenido: {token[:8]}...{token[-4:]} (len={len(token)})")

# ── PASO 4: Validar token con /api/v2/user ────────────────────────────────────
print()
print("=" * 60)
print("PASO 4: Validando token con /api/v2/user...")
status, body = request("GET", f"{PLEX_TV}/api/v2/user", extra_headers={"X-Plex-Token": token})
print(f"  HTTP {status}")
if status == 200:
    user = json.loads(body)
    print(f"  Usuario: {user.get('username') or user.get('email')}")
else:
    print(f"  ERROR: {body[:300]}")
    exit(1)

# ── PASO 5: Listar servidores ──────────────────────────────────────────────────
print()
print("=" * 60)
print("PASO 5: Obteniendo servidores Plex...")
status, body = request(
    "GET",
    f"{PLEX_TV}/api/v2/resources?includeHttps=1&includeRelay=1",
    extra_headers={"X-Plex-Token": token},
)
print(f"  HTTP {status}")
if status != 200:
    print(f"  ERROR: {body[:300]}")
    exit(1)

devices = json.loads(body)
servers = [d for d in devices if "server" in (d.get("provides") or "")]
print(f"  Dispositivos totales: {len(devices)} — Servidores: {len(servers)}")
print()
for s in servers:
    print(f"  SERVER: {s['name']!r}  owned={s.get('owned')}  presence={s.get('presence')}")
    for c in s.get("connections", []):
        print(f"    uri={c.get('uri')}  local={c.get('local')}  relay={c.get('relay')}  protocol={c.get('protocol')}")

print()
print("=" * 60)
print("✓ Flujo completo OK. Token y servidores verificados.")
print(f"  Token para pruebas: {token}")
