# Configuration Hierarchy
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Define the configuration hierarchy, validation rules, reload semantics, and precedence order for all system settings.

---

## 1. Configuration Hierarchy (Highest to Lowest Priority)

| Priority | Source | Scope | Reload | Notes |
| :--- | :--- | :--- | :--- | :--- |
| 1 (highest) | Environment variables | Process-wide | Requires restart | Overrides `.env` file |
| 2 | `.env` file | Process-wide | Requires restart | Loaded by Pydantic `Settings` |
| 3 | Pydantic `Settings` defaults | Process-wide | Requires restart | Hardcoded in `app/core/config.py` |
| 4 (lowest) | Runtime Redis state | Per-key | Immediate | Bot settings callbacks, regime state |

**Rule:** Environment variables always win. If `BYBIT_API_KEY` is set in both `.env` and the environment, the environment value is used.

---

## 2. Settings Model (`app/core/config.py`)

All configuration is loaded via Pydantic `Settings` which reads from `.env` and environment variables. The model is a singleton (`get_settings()` with `@lru_cache`).

### 2.1 Required Settings (No Defaults)

| Variable | Type | Purpose | Validation |
| :--- | :--- | :--- | :--- |
| `BYBIT_API_KEY` | `str` | Bybit API key | Required, non-empty |
| `BYBIT_API_SECRET` | `str` | Bybit API secret | Required, non-empty |

### 2.2 Optional Settings (With Defaults)

| Variable | Type | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `POSTGRES_URL` | `str` | `postgresql+asyncpg://karsa:karsa@db:5432/karsa` | PostgreSQL connection |
| `REDIS_URL` | `str` | `redis://redis:6379/0` | Redis connection |
| `TELEGRAM_BOT_TOKEN` | `str` | `""` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | `str` | `""` | Authorized Telegram chat ID |
| `DEAD_MANS_SWITCH_URL` | `str` | `""` | External health ping target |
| `DEAD_MANS_SWITCH_INTERVAL` | `int` | `60` | Ping interval in seconds |
| `9ROUTER_BASE_URL` | `str` | `http://127.0.0.1:20129` | AI proxy endpoint |
| `9ROUTER_AUTH_TOKEN` | `str` | `""` | AI proxy auth token |
| `9ROUTER_MODEL` | `str` | `claude-haiku-3-5` | Default AI model |
| `AI_ANALYST_ENABLED` | `bool` | `True` | AI analyst toggle (see CONTEXT.md Issue #8) |
| `AI_POSITION_JUDGE_ENABLED` | `bool` | `True` | AI judge toggle (see CONTEXT.md Issue #8) |
| `DAILY_DRAWDOWN_LIMIT` | `str` | `"-0.02"` | Daily drawdown limit (see CONTEXT.md Issue #2) |
| `SYMBOLS` | `List[str]` | 60 symbols (see config.py) | Trading universe |

### 2.3 Alias Support

The 9router settings support both `9ROUTER_*` and `NINE_ROUTER_*` env var prefixes via Pydantic `AliasChoices`:
```python
nine_router_base_url: str = Field(
    default="http://127.0.0.1:20129",
    validation_alias=AliasChoices("9ROUTER_BASE_URL", "nine_router_base_url"),
)
```

---

## 3. Runtime Configuration (Redis-Backed)

Some settings can be changed at runtime via Telegram bot commands without restarting the process.

| Setting | Redis Key | Telegram Command | Default | Values |
| :--- | :--- | :--- | :--- | :--- |
| Regime filter | `karsa:settings:regime_filter` | `/settings` toggle | `"1"` (enabled) | `"0"` / `"1"` |
| Max positions | `karsa:settings:max_positions` | `/settings` toggle | `"3"` | `"3"` / `"5"` / `"8"` |
| Risk profile | `karsa:state:risk_profile` | `/settings` toggle | `"conservative"` | `"conservative"` / `"semi_aggressive"` / `"aggressive"` |
| Alerts | `karsa:alerts_enabled` | `/alerts on/off` | `"1"` (enabled) | `"0"` / `"1"` |
| Global halt | `karsa:global_halt` | `/kill_karsa` / `/clear_halt` | `"0"` (not halted) | `"0"` / `"1"` |

**Reload semantics:** These settings are read on every access (no caching). Changing the Redis value takes effect immediately on the next signal cycle.

---

## 4. Configuration Validation

### 4.1 Startup Validation

Pydantic `Settings` validates on construction:
- Required fields (`bybit_api_key`, `bybit_api_secret`) must be non-empty
- Type coercion: `"60"` → `60` for `dead_mans_switch_interval`
- Invalid values raise `pydantic.ValidationError` and prevent startup

### 4.2 Runtime Validation

- Redis-backed settings are validated on read (e.g., `karsa:settings:max_positions` must be `"3"`, `"5"`, or `"8"`)
- Invalid Redis values are logged and fall back to default

### 4.3 Missing `.env` File

If `.env` is missing, Pydantic uses environment variables only. If required variables are also missing, startup fails with a clear error message.

---

## 5. Configuration Reload

### 5.1 What Requires a Process Restart

- Bybit API credentials
- PostgreSQL URL
- Redis URL
- Telegram bot token / chat ID
- Dead man's switch URL
- 9router base URL / auth token / model
- Symbol list
- Daily drawdown limit

### 5.2 What Can Be Changed at Runtime

- Regime filter toggle
- Max positions
- Risk profile
- Alerts toggle
- Global halt flag

### 5.3 What Is Determined by Code (Not Configurable)

- Signal weights (W_SKEW, W_LEAD_LAG, W_FUNDING, W_OI) — change requires code edit
- Regime thresholds (Hurst, ADX) — change requires code edit
- Risk gate thresholds (min volume, max spread) — change requires code edit
- Position lifecycle thresholds (HARD_FAIL %, TIME_STOP) — change requires code edit
- AI model selection for escalated judge — hardcoded in `position_judge.py`

---

## 6. Docker Compose Configuration

Environment variables are passed to the app container via `docker-compose.yml` `environment` section. The `.env` file is mounted at the project root.

```yaml
services:
  app:
    environment:
      - BYBIT_API_KEY=${BYBIT_API_KEY}
      - BYBIT_API_SECRET=${BYBIT_API_SECRET}
      - POSTGRES_URL=${POSTGRES_URL}
      - REDIS_URL=${REDIS_URL}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
```

**Security note:** Never commit `.env` — it's in `.gitignore`. Use `.env.example` as a template.
