# Telegram Bot Interface Specification
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft
**Purpose:** Define the Telegram mini-app interface (single `/start` command with inline keyboard navigation), alert system, and security model.

---

## 1. Overview

The Telegram bot is a **single-command mini-app**. The operator sends `/start` and navigates everything via inline keyboard buttons — no other commands needed.

**Architecture:**
- One `/start` command handler → launches dashboard
- All navigation via `CallbackQueryHandler` (inline keyboard buttons)
- Dashboard adapts based on session state (active vs inactive)
- Emergency actions (kill switch, sell all) in Control Panel with confirmation
- Edit-in-place pattern (updates existing message, not new messages)

---

## 2. Security Model

### 2.1 Authorization Boundary
Every handler (command and callback) **must** call `_is_authorized(update)` as its first line.

```python
def _is_authorized(update: Update) -> bool:
    settings = get_settings()
    if not settings.telegram_chat_id:
        return False
    return str(update.effective_chat.id) == settings.telegram_chat_id
```

### 2.2 Unauthorized Access
- Handler returns immediately (no response)
- Log warning: `logger.warning(f"Unauthorized access from chat {update.effective_chat.id}")`

---

## 3. Main Menu (`/start` → Dashboard)

### 3.1 Dashboard (Session Inactive)

When no autonomous session is running:

```
🤖 Karsa Auto Session Manager

📊 System: Idle
💰 Wallet: $10,000.00
📈 Positions: 0

[🚀 LAUNCH NEW SESSION]
[📜 Trade History]  [⚙️ Settings]
[🎛️ Control Panel]  [💼 Positions]
```

### 3.2 Dashboard (Session Active)

When autonomous session is running:

```
🤖 Karsa Auto Session Manager

📊 System: Running | Regime: TREND_BULL
💰 Wallet: $10,142.50 (+1.43%)
📈 Positions: 2 | Daily PnL: +$142.50

Session: 14h 32m remaining

[📊 Dashboard]  [📋 Activity]
[💼 Portfolio]  [🎛️ Control Panel]
[⚙️ Settings]   [📜 History]
```

### 3.3 Main Keyboard Layout

**Session inactive:**
```
[🚀 LAUNCH NEW SESSION]
[📜 Trade History]  [⚙️ Settings]
[🎛️ Control Panel]  [💼 Positions]
```

**Session active:**
```
[📊 Dashboard]  [📋 Activity]
[💼 Portfolio]  [🎛️ Control Panel]
[⚙️ Settings]   [📜 History]
[🧠 AI Status]  [🌐 Universe]
```

### 3.4 AI Status Panel (`cmd_ai_status`)

**Purpose:** Show AI analyst and position judge health.
**Data sources:** Redis (`ai:cache:*`, position store), Prometheus metrics.

```
🧠 AI Layer Status

📊 CryptoAnalyst (Stage 3)
  Model: claude-haiku-3-5
  Last call: 12s ago (245ms)
  Success: 42 | Failures: 1
  Cache hits: 8

⚖️ Position Judge (Stage 6)
  Active positions judged: 2
  Last verdict: HOLD (BTC/USDT)
  Consecutive holds: 1
  Forced exits (3-HOLD): 0

[🔙 Back to Dashboard]
```

### 3.5 Universe Panel (`cmd_universe`)

**Purpose:** Show active tradeable universe from scorer.
**Data source:** Redis (`system:universe:symbols`).

```
🌐 Active Universe

Last refresh: 3m ago
Active symbols: 12 / 60

Top 5 by score:
  1. BTC/USDT  — 87 (vol:30 mom:25 sq:22 pen:0) [L1]
  2. ETH/USDT  — 82 (vol:28 mom:22 sq:22 pen:0) [L1]
  3. SOL/USDT  — 74 (vol:24 mom:20 sq:20 pen:0) [L2]
  4. DOGE/USDT — 68 (vol:20 mom:18 sq:20 pen:0) [L3]
  5. ARB/USDT  — 61 (vol:18 mom:15 sq:18 pen:0) [L4]

Sectors: L1=2/2  L2=2/2  L3=1/2  L4=1/2

[🔄 Force Refresh]  [🔙 Back to Dashboard]
```

---

## 4. Screen Reference

### 4.1 Dashboard (`cmd_dashboard`)

**Purpose:** Main hub — system health, wallet balance, session status.
**Data sources:** Bybit REST (wallet, positions), Redis (regime, circuit breaker), Postgres (daily PnL).

**Adapts to state:**
- No session → shows "LAUNCH NEW SESSION" button
- Session active → shows session timer, daily PnL, regime

---

### 4.2 Activity Feed (`cmd_activity`)

**Purpose:** Live feed of recent signals and closed trades.
**Status:** Stub — requires signal/trade tables (pending DATA_MODEL.md §7 sign-off).

**Current behavior:** Shows placeholder message with back button.

---

### 4.3 Portfolio (`cmd_portfolio`)

**Purpose:** Open positions fetched live from Bybit.
**Data source:** `bybit.get_positions()` + `global:state:{symbol}` for current prices.

**Layout:**
```
💼 Open Positions

┌─ BTC/USDT:USDT ─────────────┐
│ Side: LONG                  │
│ Size: 0.001 BTC             │
│ Entry: $64,250.00           │
│ Current: $64,890.00         │
│ PnL: +$0.64 (+1.00%)       │
│ SL: $64,100.00              │
└─────────────────────────────┘

💰 Total Unrealized PnL: +$0.64

[🔙 Back to Dashboard]
```

---

### 4.4 Control Panel (`cmd_control`)

**Purpose:** Emergency controls and overrides.
**Authorization:** Required (critical actions).

**Layout:**
```
🎛️ DESK CONTROL PANEL

System State:
Global Halt: 🟢 INACTIVE
Cooldown: 🟢 INACTIVE
Trade Alerts: 🔔 ON

Select an operation below.

[🚨 HALT]  [💸 SELL ALL]
[▶️ RESUME]  [🔙 Back]
```

**Actions:**
| Button | Callback | Action |
|--------|----------|--------|
| HALT | `execute_kill` | Set `karsa:global_halt`, cancel all orders, flatten positions |
| SELL ALL | `execute_sellall` | Market close all positions, set 15-min cooldown |
| RESUME | `execute_resume` | Clear `karsa:global_halt` and cooldown |

---

### 4.5 Settings (`cmd_settings`)

**Purpose:** Toggle bot parameters inline.
**Data source:** Redis `karsa:settings:*` keys.

**Layout:**
```
⚙️ Settings

Max Positions: 3
Risk Profile: Conservative
Regime Filter: ✅ Enabled
Trade Alerts: 🔔 Enabled

[Max Positions: 3 ▼]
[Risk Profile ▼]
[Regime Filter: ON]
[Alerts: ON]
[🔙 Back]
```

**Callback actions:**
- `toggle_max_pos` — Cycle 3 → 5 → 8 → 3
- `toggle_risk_profile` — Cycle conservative → semi_aggressive → aggressive
- `toggle_regime` — Toggle on/off
- `toggle_alerts` — Toggle on/off

---

### 4.6 Trade History (`cmd_trade_history`)

**Purpose:** Paginated view of closed trades.
**Data source:** Postgres `trades` table.

**Layout:**
```
📜 Trade History

Page 1/3

1. BTC/USDT LONG +$0.64 (1.00%) — 2h 15m
2. ETH/USDT SHORT -$0.30 (-0.87%) — 45m
3. SOL/USDT LONG +$1.20 (2.40%) — 3h 10m

[◀️ Prev]  [Page 1/3]  [Next ▶️]
[🔙 Back]
```

---

### 4.7 Position Detail (`view_positions_detail`)

**Purpose:** Detailed position view with management actions.
**Data source:** Bybit REST + Redis position store.

**Layout:**
```
📈 Position Detail

BTC/USDT:USDT LONG
Entry: $64,250.00 | Current: $64,890.00
PnL: +$0.64 (+1.00%)
SL: $64,100.00 | TP: $65,500.00
Duration: 2h 15m

[Move SL to BE]  [Close Position]
[🔙 Back]
```

**Actions:**
| Button | Callback | Action |
|--------|----------|--------|
| Move SL to BE | `move_sl_be:{symbol}` | Amend stop-loss to entry price |
| Close Position | `close_position:{symbol}` | Market close at current price |

---

### 4.8 Universe (`universe_cmd`)

**Purpose:** Show configured trading universe.
**Status:** Stub — uses `settings.symbols` until UniverseEngine is ported.

**Layout:**
```
📡 Crypto Universe

Scanning 60 coins (from config):

  1. BTC/USDT
  2. ETH/USDT
  3. SOL/USDT
  ...

[🔙 Back to Dashboard]
```

---

## 5. Alert System (Outbound Messages)

Alerts are sent as standalone messages (not edits). Priority levels:

| Level | Prefix | Examples | Always Send? |
|-------|--------|----------|--------------|
| CRITICAL | 🚨 | Kill switch, circuit breaker, AI offline | Yes |
| WARNING | ⚠️ | Proxy degraded, stale data, sector cap | Yes |
| INFO | 📊 | Trade executed, position opened/closed | If alerts_enabled |
| AI_DECISION | 🤖 | AI analyst verdict, judge decision | If alerts_enabled |

### 5.1 Trade Alerts

**Position Opened:**
```
📈 Position Opened

Symbol: BTC/USDT:USDT
Side: LONG | Size: 0.001 BTC
Entry: $64,250.00
Confidence: 78.2%
Latency: 342ms
```

**Position Closed:**
```
📉 Position Closed

Symbol: BTC/USDT:USDT
PnL: +$0.64 (+1.00%)
Duration: 2h 15m
```

### 5.2 System Alerts

**Circuit Breaker:**
```
🛑 CIRCUIT BREAKER TRIGGERED

Daily drawdown: -2.01% (limit: -2.00%)
All positions flattened. Bot halted.
```

**AI Offline:**
```
🚨 AI OFFLINE

9router unreachable after 3 failures.
Signals halted (AI mandatory).
```

### 5.3 AI Decision Alerts

**AI Analyst Rejected Signal:**
```
🤖 AI Signal Rejected

Symbol: SOL/USDT
Deterministic confidence: 72.1%
AI confidence: 31.0%
Blended: 51.6% (below 65% gate)
Reason: Funding divergence lacks cross-exchange confirmation
```

**AI Position Judge Verdict:**
```
⚖️ Judge Verdict: TIGHTEN_STOP

Symbol: ETH/USDT:USDT (LONG)
PnL: +0.45% | Duration: 47m
Tier: cheap (haiku) → escalated (sonnet)
Reason: Momentum fading, move SL to breakeven
Consecutive holds: 0
```

**3-HOLD Forced Exit:**
```
🚨 AI Forced Exit

Symbol: ARB/USDT:USDT (LONG)
PnL: -1.2% | Duration: 3h 12m
Reason: 3 consecutive HOLD verdicts on losing position
Action: Market close triggered
```

### 5.4 Universe & Lifecycle Alerts

**Universe Refreshed:**
```
🌐 Universe Refreshed

Active: 12 symbols (was 14)
New: ARB/USDT, TIA/USDT
Dropped: AVAX/USDT, LINK/USDT
Reason: score threshold or sector cap
```

**Sector Cap Rejected:**
```
⚠️ Sector Cap Rejected

Symbol: OP/USDT (L2 sector)
Current L2 positions: 2/2 (ETH, ARB)
Signal blocked by diversity cap
```

---

## 6. Redis Key Mapping

### 6.1 Keys Read by Bot

| Key | Purpose | Screen |
|-----|---------|--------|
| `karsa:global_halt` | Halt status | Control Panel, Dashboard |
| `karsa:crypto_cooldown` | Cooldown status | Control Panel |
| `karsa:alerts_enabled` | Alert toggle | Settings, Control Panel |
| `karsa:settings:max_positions` | Max positions | Settings |
| `karsa:settings:regime_filter` | Regime filter | Settings |
| `karsa:state:risk_profile` | Risk profile | Settings |
| `global:state:{symbol}` | Market state | Portfolio, Position Detail |
| `system:circuit_breaker` | Breaker state | Dashboard |
| `system:config:regime` | Regime | Dashboard |
| `system:universe:symbols` | Universe scores | Universe Panel |
| `ai:cache:{hash}` | AI analyst cache | AI Status Panel |
| `karsa:memory:{symbol}` | Trade memory | AI Status (last injection) |
| `karsa:sector:{sector}` | Sector position count | Universe Panel |
| `karsa:position:{symbol}:{side}` | Position lifecycle state | Portfolio, Judge alerts |

### 6.2 Keys Written by Bot

| Key | Purpose | Action |
|-----|---------|--------|
| `karsa:global_halt` | Halt flag | Control Panel (HALT/RESUME) |
| `karsa:crypto_cooldown` | Cooldown | Control Panel (SELL ALL) |
| `karsa:alerts_enabled` | Alert toggle | Settings callback |
| `karsa:settings:max_positions` | Max positions | Settings callback |
| `karsa:settings:regime_filter` | Regime filter | Settings callback |
| `karsa:state:risk_profile` | Risk profile | Settings callback |

---

## 7. Error Handling

### 7.1 Dependency Unavailable
- Reply: `⚠️ [Component] unavailable. Please try again later.`
- Log: `logger.error(f"Handler error: {e}")`
- Never crash the bot process

### 7.2 Callback Query Errors
```python
try:
    # ... handler logic
except Exception as e:
    logger.error(f"Callback error: {e}")
    await query.answer("⚠️ Error occurred", show_alert=True)
```

### 7.3 Stale Callbacks
If a callback references a deleted message:
```python
except telegram.error.BadRequest as e:
    if "Message is not modified" in str(e):
        pass  # Content unchanged, ignore
    else:
        logger.warning(f"Callback stale: {e}")
```

---

## 8. Implementation Notes

### 8.1 Handler Registration
```python
# Single command handler
application.add_handler(CommandHandler("start", dashboard_cmd))

# All callbacks via pattern routing
application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(cmd_|auto_|execute_|toggle_|view_|move_|close_)"))
```

### 8.2 Edit-in-Place Pattern
```python
async def _reply(update: Update, content, **kwargs):
    """Unified reply — edits existing message or sends new."""
    if update.callback_query:
        try:
            return await update.callback_query.message.edit_text(text, **kwargs)
        except Exception:
            return await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        return await update.message.reply_text(text, **kwargs)
```

### 8.3 Keyboard Builder
```python
def build_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
         InlineKeyboardButton("📋 Activity", callback_data="cmd_activity")],
        [InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
         InlineKeyboardButton("🎛️ Control Panel", callback_data="cmd_control")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
         InlineKeyboardButton("📜 History", callback_data="cmd_trade_history")],
    ])
```

---

## 9. Testing

### 9.1 Required Tests
- Authorization boundary (authorized/unauthorized)
- All menu routes render correctly
- Settings callbacks persist to Redis
- Control Panel actions (halt, sell all, resume)
- Position management (move SL to BE, close)
- Stale callback handling
- Decimal formatting (no float in UI)

### 9.2 Test Files
- `tests/bot/test_auth.py` — Authorization boundary
- `tests/bot/test_format.py` — Formatting helpers
- `tests/bot/test_decimal_safety.py` — No float leakage
