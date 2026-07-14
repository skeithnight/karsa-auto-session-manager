# Local Development Setup
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Get a working local Docker environment running against Bybit Testnet, with WARP proxy correctly routed, per `ARCHITECTURE.md` §9.

---

## 1. Prerequisites

- Docker & Docker Compose (v2+)
- Python 3.11+ (for running tests/tools outside Docker)
- A Bybit account with **Testnet API keys** (create at the Bybit Testnet portal — separate credentials from mainnet)
- A Telegram bot token + chat ID (for the Kill Switch and alerts per `RISK_AND_RUNBOOK.md` §1)
- (Optional but recommended) A Healthchecks.io account or equivalent for the Dead Man's Switch (`RISK_AND_RUNBOOK.md` §1)

---

## 2. WARP Proxy Setup (Docker Service)

The WARP proxy runs as a Docker service (`karsa-warp`) in the same compose stack. No host-machine install needed.

```bash
# WARP starts automatically with the stack. To start only WARP:
docker compose up -d warp

# Verify WARP is connected:
docker logs karsa-warp
# Expect: "WARP proxy started on port 1080"
```

**Verify the proxy is live** before using the app:
```bash
docker compose exec warp warp-cli status
# Expect: Status: Connected
```

---

## 3. Environment Variables

Copy `.env.example` → `.env` and fill in the following. Per `DEFINITION_OF_DONE.md` §4, none of these may ever be hardcoded in source — they're loaded exclusively via `app/core/config.py`'s Pydantic `Settings`.

| Variable | Example | Notes |
| :--- | :--- | :--- |
| `WARP_PROXY_URL` | `socks5h://warp:1080` | Per `ARCHITECTURE.md` §9 — points to WARP Docker service |
| `BYBIT_API_KEY` | — | **Testnet** key — never use a mainnet key locally |
| `BYBIT_API_SECRET` | — | Testnet secret |
| `BYBIT_TESTNET` | `true` | Explicit flag so the client never silently points at mainnet |
| `POSTGRES_HOST` | `db` | Docker Compose service name |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `karsa` | |
| `POSTGRES_USER` | — | |
| `POSTGRES_PASSWORD` | — | |
| `TELEGRAM_BOT_TOKEN` | — | Used for Kill Switch commands and alerts |
| `TELEGRAM_CHAT_ID` | — | Authorized chat only — Kill Switch must ignore commands from any other chat |
| `HEALTHCHECKS_PING_URL` | — | Dead Man's Switch external ping target |
| `KILL_SWITCH_FILE_PATH` | `/tmp/KILL_KARSA` | Backup local kill trigger per `RISK_AND_RUNBOOK.md` §1 |
| `PROMETHEUS_PORT` | `9090` | `/metrics` exposure port |
| `LOG_LEVEL` | `INFO` | |
| `REDIS_URL` | *(unset)* | **Do not set unless `CONTEXT.md` Open Issue #1 (Redis scope) is resolved** — leave blank/commented in `.env.example` for now |

---

## 4. First-Time Bring-Up

```bash
# 1. Build and start the stack
docker compose build
docker compose up -d

# 2. Confirm containers are healthy
docker compose ps

# 3. Apply DB schema (matches DATA_MODEL.md §3 exactly — trades, signals, system_events)
docker compose exec app python -m app.core.migrations   # or your chosen migration tool

# 4. Confirm Postgres schema landed correctly
docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c '\dt'
```

---

## 5. Verifying WARP Routing From Inside the Container

This is the single most important check before doing anything with real (even testnet) orders — a misconfigured proxy fails silently in exactly the way `DEFINITION_OF_DONE.md` §4 anti-pattern #6 warns about ("guessing" instead of verifying).

```bash
docker compose exec app curl -x socks5h://warp:1080 \
  https://www.cloudflare.com/cdn-cgi/trace/ | grep warp
# Expect: warp=on (or warp=plus)
```

---

## 6. Smoke Test (Phase 1 Deliverable per `MVP_SCOPE.md` §5)

```bash
docker compose exec app python -m app.main --dry-run
```
Expected: continuous console output of normalized Global VWAP/Skew for BTC/ETH/SOL/BNB/XRP, and the process survives a WARP container restart (`docker compose restart warp`) without crashing — this is the actual bar from `MVP_SCOPE.md` Phase 1, not just "it started."

---

## 7. Common Setup Issues

| Symptom | Likely Cause | Fix |
| :--- | :--- | :--- |
| `curl` to Cloudflare trace endpoint hangs or times out | WARP container not connected | `docker logs karsa-warp`; restart with `docker compose restart warp` |
| Bybit REST calls return `401`/`403` | Testnet key used against mainnet endpoint, or vice versa | Confirm `BYBIT_TESTNET=true` and the key was generated on the Testnet portal, not mainnet |
| `Decimal` vs `float` errors on startup | `.env` numeric values being parsed as `float` by a misconfigured Pydantic field | Check `app/core/config.py` — all price/size settings must type as `Decimal`, per `DATA_MODEL.md` §1 |
| Postgres container healthy but app can't connect | `POSTGRES_HOST` set to `localhost` instead of the Compose service name | Use the Docker Compose service name (e.g. `db`), not `localhost`, from inside the app container |

---

## 8. Security Notes

- Never commit `.env` — confirm it's in `.gitignore` before first commit.
- Bybit API key should be scoped to **trading only** — disable withdrawal permissions entirely, and use an IP allowlist if your Bybit tier supports it.
- Telegram bot token should be treated as a credential, not a config value — same handling as the Bybit secret.
- Rotate all of the above if `.env` is ever accidentally exposed (git history, shared screen, etc.) — treat rotation as mandatory, not optional cleanup.