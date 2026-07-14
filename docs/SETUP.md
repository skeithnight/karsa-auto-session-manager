# Local Development Setup
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Get a working local Docker environment running with WireGuard VPN, Prometheus metrics, and Grafana dashboards.

---

## 1. Prerequisites

- Docker & Docker Compose (v2+) — Rancher Desktop or Docker Desktop
- Python 3.11+ (for running tests/tools outside Docker)
- A Bybit account with API keys (mainnet — asset read permission required for reconciliation)
- A Telegram bot token + chat ID (for alerts per `RISK_AND_RUNBOOK.md` §1)
- A DigitalOcean droplet (or equivalent) running WireGuard VPN server

---

## 2. VPN Setup (WireGuard via Gluetun)

All outbound traffic routes through a WireGuard VPN tunnel via the `gluetun` Docker service.

### Server (DigitalOcean droplet)
```bash
# Install WireGuard
apt install wireguard -y

# Generate server keys
wg genkey | tee /etc/wireguard/server_private | wg pubkey > /etc/wireguard/server_public

# Configure /etc/wireguard/wg0.conf
# Address = 10.10.0.1/24, ListenPort = 51820
# Peer: client public key, AllowedIPs = 10.10.0.2/32
# PostUp/PostDown: iptables MASQUERADE on eth0

# Enable and start
wg-quick up wg0 && systemctl enable wg-quick@wg0

# Firewall: allow UDP 51820
ufw allow 51820/udp
# Also add rule in DigitalOcean Cloud Firewall (Networking → Firewalls)
```

### Client (.env)
```bash
WIREGUARD_PRIVATE_KEY=<client_private_key>
WIREGUARD_PUBLIC_KEY=<server_public_key>
WIREGUARD_ADDRESSES=10.10.0.2/32
VPN_ENDPOINT_IP=<droplet_ip>
VPN_ENDPOINT_PORT=51820
BACKEND_SUBNET=172.28.0.0/16
```

### Verify VPN
```bash
docker logs karsa-gluetun
# Expect: "Public IP address is <droplet_ip>"
# Expect: "ready and using plain DNS resolvers: [1.1.1.1:53]"
```

---

## 3. DNS Bypass (ISP Poisoning)

Some ISPs (e.g. Telkomsel) poison DNS for crypto exchange domains. The app includes a Python-level DNS bypass (`app/main.py`) that queries:
1. **Gluetun DNS (127.0.0.1)** — forwards to Cloudflare 1.1.1.1 via VPN (not poisoned)
2. **Docker DNS (127.0.0.11)** — resolves internal names (db, redis, 9router)
3. **System resolver** — fallback

Additionally, `entrypoint.sh` writes `nameserver 127.0.0.1` to `/etc/resolv.conf` at container startup.

---

## 4. Environment Variables

Copy `.env.example` → `.env` and fill in the following. Per `DEFINITION_OF_DONE.md` §4, none of these may ever be hardcoded in source — they're loaded exclusively via `app/core/config.py`'s Pydantic `Settings`.

| Variable | Example | Notes |
| :--- | :--- | :--- |
| `BYBIT_API_KEY` | — | Mainnet key — needs "Asset" read permission for reconciliation |
| `BYBIT_API_SECRET` | — | Mainnet secret |
| `POSTGRES_URL` | `postgresql+asyncpg://karsa:karsa@db:5432/karsa` | Async PostgreSQL |
| `REDIS_URL` | `redis://redis:6379/0` | In-memory state store |
| `TELEGRAM_BOT_TOKEN` | — | Used for alerts |
| `TELEGRAM_CHAT_ID` | — | Authorized chat only |
| `DEAD_MANS_SWITCH_URL` | — | External health ping target |
| `9ROUTER_BASE_URL` | `http://127.0.0.1:20129` | AI proxy for analyst/judge |
| `9ROUTER_AUTH_TOKEN` | — | 9router auth |
| `9ROUTER_MODEL` | `claude-haiku-3-5` | AI model for off-hot-path analysis |
| `WIREGUARD_PRIVATE_KEY` | — | Client WireGuard key |
| `WIREGUARD_PUBLIC_KEY` | — | Server WireGuard public key |
| `WIREGUARD_ADDRESSES` | `10.10.0.2/32` | Client tunnel IP |
| `VPN_ENDPOINT_IP` | — | Droplet IP |
| `VPN_ENDPOINT_PORT` | `51820` | WireGuard port |
| `BACKEND_SUBNET` | `172.28.0.0/16` | Docker network subnet |

---

## 5. First-Time Bring-Up

```bash
# 1. Build and start the full stack
docker compose up -d --build

# 2. Confirm all containers running
docker compose ps
# Expect: karsa-app, karsa-gluetun, karsa-db, karsa-redis, karsa-prometheus, karsa-grafana, karsa-9router

# 3. Verify VPN tunnel
docker logs karsa-gluetun | grep "Public IP"
# Expect: Public IP address is <droplet_ip>

# 4. Verify Prometheus scraping
curl -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=up{job="karsa"}'
# Expect: value "1"

# 5. Open Grafana
open http://localhost:3000
# Default dashboards: data-ingestion, operations, signal-confidence
```

---

## 6. Verifying VPN Routing

```bash
# Check public IP through VPN
docker exec karsa-gluetun wget -q -O- --timeout=10 http://ifconfig.me
# Expect: <droplet_ip>, NOT your local ISP IP

# Check DNS resolution (should NOT return Telkomsel captive portal IP)
docker exec karsa-app python3 -c "import socket; print(socket.getaddrinfo('api.binance.com', 443)[:1])"
# Expect: real Binance IP (e.g. 108.158.x.x), not 202.3.218.137
```

---

## 7. Smoke Test

```bash
# Check data ingestion metrics
curl -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=karsa_orderbook_received_total'
# Expect: non-zero values per exchange/symbol

# Check alpha metrics
curl -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=karsa_regime_state'
# Expect: regime value (0=CHOP, 1=MR, 2=BEAR, 3=BULL)

# Check Grafana dashboards
# Navigate to http://localhost:3000/d/karsa-data-ingestion/
# Verify: VWAP, Skew, Heartbeat panels show real data
```

---

## 8. Common Setup Issues

| Symptom | Likely Cause | Fix |
| :--- | :--- | :--- |
| App crashes with DNS errors | ISP DNS poisoning (Telkomsel) | Verify `entrypoint.sh` is in Dockerfile; check gluetun DNS is running |
| Gluetun in restart loop | WireGuard handshake failing | Check AllowedIPs match client address; verify DO Cloud Firewall allows UDP 51820 |
| Exchange API SSL errors | DNS returning wrong IP (ISP captive portal) | DNS bypass should handle this; check `docker logs karsa-gluetun` for DNS status |
| Prometheus `up=0` | Port mismatch | App uses port 8001 (not 8000); verify `prometheus.yml` targets `gluetun:8001` |
| `Bybit connected` but reconciliation fails | API key lacks "Asset" permission | Edit API key in Bybit → enable "Asset" read permission |
| `Decimal` vs `float` errors | `.env` numeric values parsed as float | Check `app/core/config.py` — all price/size settings must type as `Decimal` |
| Postgres container healthy but app can't connect | `POSTGRES_HOST` set to `localhost` instead of the Compose service name | Use the Docker Compose service name (e.g. `db`), not `localhost`, from inside the app container |

---

## 8. Security Notes

- Never commit `.env` — confirm it's in `.gitignore` before first commit.
- Bybit API key should be scoped to **trading only** — disable withdrawal permissions entirely, and use an IP allowlist if your Bybit tier supports it.
- Telegram bot token should be treated as a credential, not a config value — same handling as the Bybit secret.
- Rotate all of the above if `.env` is ever accidentally exposed (git history, shared screen, etc.) — treat rotation as mandatory, not optional cleanup.