# Telegram Bot Interface Specification
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft
**Purpose:** Define the complete Telegram command interface, alert system, and security model for Key 7.

---

## 1. Overview

The Telegram bot serves as the **operator control panel** for the trading system. It provides:
1. **Real-time alerts** — trade executions, circuit breaker events, system status
2. **Manual controls** — kill switch, halt clearing, settings adjustments
3. **Status queries** — positions, PnL, system health

The bot runs as a concurrent `asyncio` task alongside the 6 core trading keys. It uses `python-telegram-bot` (PTB) v20+ with an asyncio-native polling loop.

---

## 2. Security Model

### 2.1 Authorization Boundary
Every public handler **must** call `_is_authorized(update)` as its first line. No exceptions.

```python
def _is_authorized(update: Update) -> bool:
    """Single security boundary — checks TELEGRAM_CHAT_ID."""
    settings = get_settings()
    if not settings.telegram_chat_id:
        return False
    return str(update.effective_chat.id) == settings.telegram_chat_id
```

### 2.2 Unauthorized Access
If `_is_authorized()` returns `False`:
- Handler returns immediately (no response)
- Log warning: `logger.warning(f"Unauthorized access attempt from chat {update.effective_chat.id}")`
- Do NOT reveal system existence or functionality to unauthorized users

### 2.3 Secret Management
- `TELEGRAM_BOT_TOKEN` — loaded from `.env` via Pydantic Settings, never hardcoded
- `TELEGRAM_CHAT_ID` — loaded from `.env` via Pydantic Settings, never hardcoded
- If either is empty/missing, bot fails to start with clear error message

---

## 3. Bot Startup & Lifecycle

### 3.1 Initialization (runner.py)
```python
async def run_bot(redis_client: RedisClient, bybit_client: BybitClient, kill_switch: asyncio.Event):
    """Build and start PTB application."""
    settings = get_settings()

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # Wire dependencies into bot_data
    application.bot_data["redis_client"] = redis_client
    application.bot_data["bybit_client"] = bybit_client
    application.bot_data["kill_switch"] = kill_switch

    # Register handlers
    register_handlers(application)

    # Start polling
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Wait for kill switch
    await kill_switch.wait()

    # Graceful shutdown (must complete within 5s)
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
```

### 3.2 Kill Switch Integration
- PTB polling loop respects global `kill_switch` asyncio.Event
- On `kill_switch.set()`, bot calls `application.stop()` within 5 seconds
- Bot sends final "🚨 KILL SWITCH ACTIVATED" alert before shutdown (if possible)

### 3.3 Bot Data Access
All shared state accessed via `context.bot_data`:
- `context.bot_data["redis_client"]` — RedisClient instance
- `context.bot_data["bybit_client"]` — BybitClient instance
- `context.bot_data["kill_switch"]` — asyncio.Event

Never use globals. Never import client instances directly.

---

## 4. Command Reference

### 4.1 Emergency Commands

#### `/kill_karsa`
**Purpose:** Emergency halt — cancel all orders, flatten all positions, stop bot.
**Priority:** CRITICAL — must execute in < 10 seconds
**Authorization:** Required

**Flow:**
1. Handler sets global `kill_switch` Event
2. Main loop detects event, executes Kill Switch Sequence:
   - Cancel all open limit orders
   - Market Flatten (IOC) all open positions
   - Set `karsa:global_halt` = `"1"` in Redis
   - Send final alert: "🚨 KILL SWITCH ACTIVATED. All positions flattened. Bot halted."
   - `sys.exit(1)`

**Response:**
```
🚨 EXECUTING KILL SWITCH...

⏳ Cancelling all open orders...
⏳ Flattening all positions...
✅ Kill switch complete. Bot halted.

Check Bybit UI to confirm flat state.
Restart bot manually when ready.
```

**Redis Keys:**
- Sets: `karsa:global_halt` = `"1"`

---

#### `/clear_halt`
**Purpose:** Clear emergency halt flag and resume normal operation.
**Priority:** HIGH
**Authorization:** Required

**Flow:**
1. Verify `karsa:global_halt` is currently `"1"`
2. Clear `karsa:global_halt` from Redis
3. Confirm with operator

**Response:**
```
✅ Halt cleared. Bot ready to resume.
⚠️ Ensure all positions are flat before restarting.
```

**Redis Keys:**
- Reads: `karsa:global_halt`
- Deletes: `karsa:global_halt`

---

### 4.2 Status Commands

#### `/status`
**Purpose:** Show current system status overview.
**Priority:** NORMAL
**Authorization:** Required

**Response:**
```
📊 System Status

🔄 Data Engine: ✅ Active
🧠 Alpha Bridge: ✅ Active
🛡️ Risk Gate: ✅ Active
⚡ Executor: ✅ Connected
📡 Watchdog: ✅ Monitoring

📈 Open Positions: 2
💰 Daily PnL: +$142.50 (+0.47%)
⏱️ Uptime: 14h 32m

🔌 WARP Proxy: ✅ Connected
🗄️ Redis: ✅ Connected
🐘 PostgreSQL: ✅ Connected
```

**Data Sources:**
- Component status: in-memory flags from each Key
- Open positions: `state_manager.get_all_positions()`
- Daily PnL: `circuit_breaker.daily_pnl`
- Uptime: process start time calculation
- Connections: health check pings

---

#### `/positions`
**Purpose:** List all open positions with current PnL.
**Priority:** NORMAL
**Authorization:** Required

**Response:**
```
📈 Open Positions (2)

┌─ BTC/USDT:USDT ─────────────┐
│ Side: LONG                  │
│ Size: 0.001 BTC             │
│ Entry: $64,250.00           │
│ Current: $64,890.00         │
│ PnL: +$0.64 (+1.00%)       │
└─────────────────────────────┘

┌─ ETH/USDT:USDT ─────────────┐
│ Side: SHORT                 │
│ Size: 0.05 ETH              │
│ Entry: $3,450.00            │
│ Current: $3,420.00          │
│ PnL: +$1.50 (+0.87%)       │
└─────────────────────────────┘

💰 Total Unrealized PnL: +$2.14
```

**Data Sources:**
- `state_manager.get_all_positions()`
- Current prices from Redis `global:state:{symbol}`

---

#### `/pnl`
**Purpose:** Show daily PnL summary.
**Priority:** NORMAL
**Authorization:** Required

**Response:**
```
💰 Daily PnL Summary

Realized: +$89.25
Unrealized: +$53.25
─────────────────
Total: +$142.50 (+0.47%)

Trades Today: 8 (6W / 2L)
Win Rate: 75.0%

Circuit Breaker: ✅ OK
Daily Limit: -2.0% (-$620.00)
Current Drawdown: -0.47%
```

**Data Sources:**
- Realized PnL: query from `trades` table (Postgres)
- Unrealized: sum of position unrealized PnL
- Circuit breaker state: `circuit_breaker.get_state()`

---

#### `/risk`
**Purpose:** Show current risk parameters and circuit breaker status.
**Priority:** NORMAL
**Authorization:** Required

**Response:**
```
🛡️ Risk Status

Circuit Breaker: ✅ ACTIVE
Daily PnL: +$142.50 (limit: -2.0%)
Consecutive Losses: 0 (max: 3)
Execution Latency: 342ms (limit: 1500ms)

Open Orders: 1
Margin Used: 15.2% (limit: 40%)

Last Reconciliation: 14h 32m ago
```

**Data Sources:**
- `circuit_breaker.get_state()`
- Exchange info from Bybit REST

---

### 4.3 Settings Commands

#### `/settings`
**Purpose:** Show current bot settings with inline keyboard for adjustments.
**Priority:** NORMAL
**Authorization:** Required

**Response:**
```
⚙️ Current Settings

Max Positions: 3
Risk Profile: Conservative
Regime Filter: ✅ Enabled
Trade Alerts: ✅ Enabled

[Modify Settings]
```

**Inline Keyboard:**
```
[Max Positions: 3 ▼]  [Risk Profile ▼]
[Regime Filter: ON]    [Alerts: ON]
[Close]
```

**Callback Queries:**
- `settings:max_positions` — Cycle through 3 → 5 → 8 → 3
- `settings:risk_profile` — Cycle through conservative → semi_aggressive → aggressive
- `settings:regime_filter` — Toggle 1/0
- `settings:alerts` — Toggle 1/0
- `settings:close` — Delete message

**Redis Keys:**
- Reads/Writes: `karsa:settings:max_positions`
- Reads/Writes: `karsa:settings:regime_filter`
- Reads/Writes: `karsa:alerts_enabled`

---

#### `/alerts on` / `/alerts off`
**Purpose:** Toggle trade alert notifications.
**Priority:** LOW
**Authorization:** Required

**Response:**
```
✅ Trade alerts enabled.
```
or
```
⏸️ Trade alerts disabled. System alerts (circuit breaker, kill switch) will still be sent.
```

**Redis Keys:**
- Reads/Writes: `karsa:alerts_enabled`

---

### 4.4 Utility Commands

#### `/help`
**Purpose:** Show available commands.
**Priority:** LOW
**Authorization:** Required

**Response:**
```
📖 Available Commands

🚨 Emergency
/kill_karsa - Emergency halt (flatten all + stop)
/clear_halt - Clear halt flag

📊 Status
/status - System status overview
/positions - Open positions
/pnl - Daily PnL summary
/risk - Risk parameters

⚙️ Settings
/settings - View/modify settings
/alerts on|off - Toggle trade alerts

❓ Help
/help - Show this message
```

---

#### `/start`
**Purpose:** Welcome message and initial status.
**Priority:** LOW
**Authorization:** Required

**Response:**
```
🤖 Karsa Auto Session Manager

Bot started and monitoring markets.
Use /help to see available commands.

📊 Quick Status: /status
```

---

## 5. Alert System (Outbound Messages)

### 5.1 Alert Priority Levels

| Level | Prefix | Examples | Always Send? |
|-------|--------|----------|--------------|
| CRITICAL | 🚨 | Kill switch, circuit breaker triggered, state divergence | Yes — overrides alerts_off |
| WARNING | ⚠️ | Proxy degradation, stale data, high latency | Yes — overrides alerts_off |
| INFO | 📊 | Trade executed, position opened/closed | Only if alerts_enabled |

### 5.2 Trade Alerts

#### Position Opened
```
📈 Position Opened

Symbol: BTC/USDT:USDT
Side: LONG
Size: 0.001 BTC
Entry: $64,250.00
Signal Confidence: 78.2%

Risk Gate: ✅ Passed
Latency: 342ms
```

#### Position Closed
```
📉 Position Closed

Symbol: BTC/USDT:USDT
Side: LONG
Size: 0.001 BTC
Entry: $64,250.00 → Exit: $64,890.00
PnL: +$0.64 (+1.00%)

Duration: 2h 15m
```

#### Order Filled
```
✅ Order Filled

Symbol: ETH/USDT:USDT
Type: Post-Only Limit
Side: SELL
Size: 0.05 ETH
Price: $3,450.00
Order ID: abc123

SOR Step: 1 (Post-Only)
```

### 5.3 System Alerts

#### Circuit Breaker Triggered
```
🛑 CIRCUIT BREAKER TRIGGERED

Reason: Daily drawdown exceeded
PnL: -$625.00 (-2.01%)
Limit: -2.00%

Action: All positions flattened, bot halted.
Manual restart required.
```

#### Proxy Degraded
```
⚠️ WARP Proxy Degraded

Bybit WebSocket disconnected.
Open orders cancelled.
Existing positions protected by exchange-side SL.

Bot paused. Attempting reconnect every 30s.
```

#### Stale Data
```
⚠️ Stale Data Detected

Exchange: Binance
Last Update: 18s ago (threshold: 15s)

Alpha Bridge paused. No new entries.
```

#### Startup Reconciliation
```
🔄 Startup Reconciliation

Scenario: Clean
Positions synced: 2
Orders synced: 1
Duration: 1.2s

✅ Ready to trade.
```

---

## 6. Redis Key Mapping

### 6.1 Keys Read by Bot

| Key | Purpose | Handler |
|-----|---------|---------|
| `karsa:global_halt` | Check if halt active | `/status`, `/clear_halt` |
| `karsa:alerts_enabled` | Check alert toggle | `/settings`, alert dispatch |
| `karsa:settings:max_positions` | Read max positions | `/settings` |
| `karsa:settings:regime_filter` | Read regime filter | `/settings` |
| `karsa:state:risk_profile` | Read risk profile | `/settings`, `/risk` |
| `global:state:{symbol}` | Current market state | `/positions` |
| `system:circuit_breaker` | Circuit breaker state | `/risk` |

### 6.2 Keys Written by Bot

| Key | Purpose | Handler |
|-----|---------|---------|
| `karsa:global_halt` | Set/clear halt flag | `/kill_karsa`, `/clear_halt` |
| `karsa:alerts_enabled` | Toggle alerts | `/alerts on/off` |
| `karsa:settings:max_positions` | Update max positions | Settings callback |
| `karsa:settings:regime_filter` | Toggle regime filter | Settings callback |
| `karsa:state:risk_profile` | Update risk profile | Settings callback |

### 6.3 Keys Read from Other Components

| Key | Written By | Read By Bot For |
|-----|------------|-----------------|
| `global:state:{symbol}` | Data Engine | `/positions` (current prices) |
| `system:heartbeat` | Watchdog | `/status` (component health) |
| `system:circuit_breaker` | Circuit Breaker | `/risk` (breaker state) |

---

## 7. Stub Subsystems

The following subsystems are documented but **not yet implemented**. Handlers that depend on them return a user-visible warning.

### 7.1 Autonomous Session Manager (ASM)
- Redis keys: `karsa:auto:state:active`, `karsa:auto:config`, `karsa:auto:start_time`, `karsa:auto:pending_duration_min`
- Status: Not ported
- Handler behavior: Return `⚠️ Autonomous Session Manager not yet available.`

### 7.2 Universe Engine
- Purpose: Dynamic Top-N symbol selection
- Status: Not ported
- Handler behavior: Return `⚠️ Universe Engine not yet available.`

### 7.3 Performance Tracker
- Purpose: Win rate, Sharpe ratio, drawdown analytics
- Status: Not ported
- Handler behavior: Return `⚠️ Performance Tracker not yet available.`

### 7.4 Stub Pattern
```python
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for unported subsystem."""
    if not _is_authorized(update):
        return

    logger.warning("Handler called but subsystem not ported")
    await update.message.reply_text("⚠️ Not yet available.")
```

---

## 8. Error Handling

### 8.1 Dependency Unavailable
If Bybit, Redis, or Postgres is unreachable:
- Reply with user-visible degradation: `⚠️ [Component] unavailable. Please try again later.`
- Log the cause: `logger.error(f"Handler error: {e}")`
- Do NOT crash the bot process

### 8.2 Handler Exceptions
Every `except` block must:
1. Log the error: `logger.error(f"Handler error: {e}")`
2. Reply to user: `⚠️ An error occurred. Please try again.`
3. Never silently pass

```python
try:
    # ... handler logic
except Exception as e:
    logger.error(f"Handler error: {e}")
    await update.message.reply_text("⚠️ An error occurred. Please try again.")
```

### 8.3 Rate Limiting
- PTB handles Telegram API rate limits automatically
- No custom rate limiting required for MVP
- Future: per-user rate limit if multiple operators

---

## 9. Message Formatting

### 9.1 HTML Formatting
All messages use HTML (not Markdown) for consistent rendering:
- `<b>bold</b>` for labels
- `<i>italic</i>` for secondary info
- `<code>inline code</code>` for values
- `<pre>preformatted</pre>` for tables

### 9.2 Formatting Helpers (utils/format.py)
```python
def bold(text: str) -> str:
    return f"<b>{text}</b>"

def italic(text: str) -> str:
    return f"<i>{text}</i>"

def code(text: str) -> str:
    return f"<code>{text}</code>"

def pre(text: str) -> str:
    return f"<pre>{text}</pre>"
```

### 9.3 Decimal Formatting
All prices/PnL rendered from `Decimal` — never `float`:
```python
def format_price(value: Decimal) -> str:
    """Format price with comma separators."""
    return f"${value:,.2f}"

def format_pnl(value: Decimal) -> str:
    """Format PnL with sign and color indicator."""
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.2f}"
```

---

## 10. Testing Requirements

### 10.1 Unit Tests (tests/bot/)
- `test_auth.py` — Authorization boundary tests
- `test_format.py` — Formatting helper tests
- `test_decimal_safety.py` — Ensure no float leakage in UI

### 10.2 Required Test Cases

#### Authorization
- Authorized user → handler executes
- Unauthorized user → handler returns silently
- Empty chat_id config → bot fails to start

#### Kill Switch
- `/kill_karsa` sets `kill_switch` Event
- `/kill_karsa` sets `karsa:global_halt` in Redis
- `/clear_halt` clears `karsa:global_halt`
- `/clear_halt` when no halt → appropriate message

#### Settings
- `/settings` returns current values
- Settings callback cycles through options
- Settings persist to Redis

#### Decimal Safety
- No `float` values in rendered messages
- All prices use `Decimal` formatting
- PnL calculations use `Decimal`

---

## 11. Future Enhancements (Post-MVP)

Not in scope for V1.0, documented for reference:

1. **Inline Queries** — Quick position lookup via inline bot
2. **Group Chat Support** — Multiple authorized operators
3. **Custom Alerts** — User-defined alert thresholds
4. **Trade Journal** — Post-trade notes via Telegram
5. **Chart Screenshots** — Automated TradingView chart captures
6. **Voice Commands** — Voice-to-text for hands-free operation
