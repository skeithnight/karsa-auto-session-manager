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
⚙️ SYSTEM DASHBOARD

DB 🟢   Redis 🟢   Bybit 🟢   VPN 🟢

Balance   $ 10,000.00
Available $ 10,000.00
Deployed  $      0.00  [░░░░░░░░░░░░] 0.0%

Session  ⚫ IDLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[🚀 LAUNCH NEW SESSION]
[📜 Trade History]  [⚙️ Settings]
[🎛️ Control Panel]  [💼 Positions]
```

### 3.2 Dashboard (Session Active)

When autonomous session is running:

```
⚙️ SYSTEM DASHBOARD

DB 🟢   Redis 🟢   Bybit 🟢   VPN 🟢

Balance   $ 10,142.50
Available $  6,142.50
Deployed  $  4,000.00  [█████░░░░░░░] 39.4%

Session  🟢 ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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

BTC/USDT  ETH/USDT  SOL/USDT  DOGE/USDT ARB/USDT
OP/USDT   LINK/USDT AVAX/USDT MATIC/USDT ADA/USDT

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

**Purpose:** Live feed of recent signals and closed trades from Redis event stream.

**Layout:**
```
📋 LIVE ACTIVITY FEED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Session  🟢 ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 Last Events
  • 10:24  AI Signal Rejected (Funding divergence)
  • 10:22  Position Closed: ETH/USDT (+1.2%)
  • 10:15  New Position: SOL/USDT (LONG)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ Full event log active once trade tables are wired

[🔄 Refresh]
[💼 Positions] [📜 History]
[🔙 Dashboard]
```

---

### 4.3 Portfolio (`cmd_portfolio`)

**Purpose:** Open positions fetched live from Bybit.
**Data source:** `bybit.fetch_positions()` + `karsa:position:{symbol}:{side}` for duration.

**Layout:**
```
💼 POSITIONS  ·  2 open
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sym   Side  Qty    Entry     uPnL   Dur
───────────────────────────────────────
BTC   L     0.001  64,250.00 🟢$+0.64 2h
ETH   S     0.5    3,450.00  🔴$-1.20 45m

Net uPnL  🔴 $-0.56
Win Rate  [████████░░░░]  50.0%  1/2

[📈 Position Detail] [🔄 Refresh]
[🎛️ Control Panel] [📜 History]
[🔙 Dashboard]
```

---

### 4.4 Control Panel (`cmd_control`)

**Purpose:** Emergency controls and overrides.
**Authorization:** Required (critical actions).

**Layout:**
```
🎛️ DESK CONTROL PANEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

System State
Global Halt   🟢 INACTIVE
Cooldown      🟢 INACTIVE
Trade Alerts  🔔 ON

Risk Gates
Max Positions  3
Regime Filter  ON  ✅
AI Analyst     MANDATORY 🔒

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  Emergency actions below are IRREVERSIBLE

[🚨 HALT]  [💸 SELL ALL]
[▶️ RESUME]  [⚙️ Settings]
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
⚙️ BOT SETTINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Parameter              Value  Cycle
─────────────────────────────────────────────
Max Open Positions     3      [3 · 5 · 8]
Regime Filter          ON     [ON · OFF]
Trade Alerts           ON     [ON · OFF]

Tap a button below to cycle the value.

[📂 Max Pos: 3] [📊 Regime: ON]
[🔔 Alerts: ON] [🎛️ Control Panel]
[🔙 Dashboard]
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
📜 TRADE HISTORY  (Page 1/3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. BTC/USDT (L)     🟢 $+12.50 (1.20%)
   2h 15m           2026-07-15 14:30
2. ETH/USDT (S)     🔴 $-5.30 (-0.87%)
   45m              2026-07-15 11:20

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trades    20W / 10L  ·  Total: 30
Win Rate  [██████████░░░░░]  66.7%
Net PnL   🟢 $+145.20  ·  Avg: $+4.84

[◀️ Prev]  [Page 1/3]  [Next ▶️]
[🔙 Back]
```

---

### 4.7 Position Detail (`view_positions_detail`)

**Purpose:** Detailed position view with management actions.
**Data source:** Bybit REST + Redis position store.

**Layout:**
```
📈 POSITION DETAIL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 Allocation
Equity  $ 10,142.50  |  Positions: 2
Cash    $  6,142.50  [█████████░░░░░░] 60.6%
Deployed $  4,000.00  [██████░░░░░░░░░] 39.4%

1. BTC/USDT (LONG) 🟢
┣ Entry: $64,250.00 → Mark: $64,890.00
┣ Size: 0.001  |  Liq: $0.00
┗ PnL: 🟢 $+0.64 (+1.00%)
  📊 Alloc: █████░░░░░ 25.0%
  SL: $64,100.00  |  TP: $65,500.00
  📉 Risk to SL: -0.23%  |  R:R: 1:8.3

[🏃 Close BTC/USDT] [🛡️ SL→BE BTC/USDT]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 SL→BE shifts Stop Loss to Entry Price — risk-free.

[🔄 Refresh] [📊 Table View]
[🔙 Dashboard]
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

**Position Opened (Entry Filled):**
```
✅ ENTRY FILLED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Metric       Value
────────────────────────────────
Symbol       BTC/USDT (Buy)
Fill Price   $64,250.00
Size         0.001
Stop Loss    $64,100.00
Max Loss     $1.00
```

**Position Closed (Take Profit / Stop Loss):**
```
🎯 TAKE PROFIT HIT 🎯
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Metric       Value
────────────────────────────────
Symbol       BTC/USDT (Buy)
Exit Price   $65,500.00
PnL          $+1.25 (+1.95%)

🟢 Position closed in profit.
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
