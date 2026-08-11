# Local & Container Development Setup
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Prerequisites

- Docker Engine & Docker Compose (v2+)
- Python 3.11+ (for local host verification or CLI commands)
- Bybit API Credentials (linear perpetual account with asset read & order write permissions)
- Telegram Bot Token & Chat ID (for alert service and single-owner command bot)

---

## 2. Environment Configuration (`.env`)

Copy `.env.example` to `.env` in the repository root and configure required credentials:

```bash
# ── Bybit API Credentials ──────────────────────────────────
BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_api_secret
BYBIT_TESTNET=false

# ── Telegram Bot & Alerts ──────────────────────────────────
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# ── 9Router AI Proxy ───────────────────────────────────────
NINE_ROUTER_BASE_URL=http://karsa-9router:20129
NINE_ROUTER_AUTH_TOKEN=your_9router_auth_token
NINE_ROUTER_MODEL=asm-combo
```

---

## 3. Starting the Multi-Container Stack

Karsa ASM uses a decoupled two-tier Compose structure:

### Step 1: Launch Infrastructure Services
```bash
docker compose -f docker-compose.infra.yml up -d
```
Starts `karsa-postgres`, `karsa-redis`, `karsa-gluetun`, `karsa-9router`, `karsa-prometheus`, and `karsa-grafana`.

### Step 2: Launch Application Services
```bash
docker compose -f docker-compose.apps.yml up -d
```
Starts `karsa-live`, `karsa-shadow`, `karsa-data-engine`, `karsa-commander`, and `karsa-backtest`.

---

## 4. Verification Commands

### Check Container Status
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Verify Live Core Loop
```bash
docker logs --tail 50 karsa-live
```
Look for: `Bybit connected (LIVE), 713 symbols mapped` and `OHLCV fetched`.

### Verify Telegram Commander Bot
```bash
docker logs --tail 50 karsa-commander
```
Look for: `bot_data wired` and `bot_polling_started`.

### Run Unit Test Suite
```bash
pytest
```