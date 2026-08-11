# Configuration Reference
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Overview & Precedence Hierarchy

System configuration is managed via Pydantic `Settings` (`app/core/config.py`), reading from `.env` files and process environment variables.

**Precedence Order (Highest to Lowest):**
1. Process Environment Variables (e.g. set by Docker `environment:`)
2. `.env` File variables
3. Hardcoded defaults in Pydantic `Settings`
4. Runtime Redis keys (`karsa:settings:*`)

---

## 2. Key Environment Settings Reference

| Category | Variable Name | Default Value | Description |
| :--- | :--- | :--- | :--- |
| **Bybit** | `BYBIT_API_KEY` | `""` | Bybit REST & WS API key |
| **Bybit** | `BYBIT_API_SECRET` | `""` | Bybit REST & WS API secret |
| **Bybit** | `BYBIT_TESTNET` | `false` | Enable Bybit testnet endpoints |
| **Role** | `KARSA_ROLE` | `"data-engine"` | Container role: `live`, `shadow`, `data-engine`, `commander`, `backtest` |
| **Shadow** | `SHADOW_MODE_ENABLED` | `false` | Enable shadow simulation mode |
| **Telegram** | `TELEGRAM_BOT_TOKEN` | `""` | Telegram bot token for alert service & commander |
| **Telegram** | `TELEGRAM_CHAT_ID` | `""` | Authorized operator chat ID |
| **AI Proxy** | `NINE_ROUTER_BASE_URL` | `"http://karsa-9router:20129"` | Base URL for local `9router` proxy |
| **AI Proxy** | `NINE_ROUTER_AUTH_TOKEN` | `""` | Authorization token for `9router` |
| **AI Proxy** | `NINE_ROUTER_MODEL` | `"asm-combo"` | Target AI model alias |
| **Database** | `POSTGRES_URL` | `"postgresql+asyncpg://karsa:karsa@postgres:5432/karsa"` | PostgreSQL connection string |
| **Redis** | `REDIS_URL` | `"redis://redis:6379/0"` | Redis connection string |
