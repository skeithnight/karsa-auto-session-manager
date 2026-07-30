# Karsa ASM - Telegram Interface Implementation Plan

## Hybrid Intelligence Trading System Dashboard

**Version:** 2.0  
**Objective:** Create comprehensive Telegram interface for monitoring, controlling, and understanding the Hybrid Intelligence Trading System  
**Target:** User-friendly interface that shows end-to-end system flow and performance

---

## 📋 Table of Contents

1. [Telegram Bot Architecture](#1-telegram-bot-architecture)
2. [Command Structure & Handlers](#2-command-structure--handlers)
3. [Dashboard Design (/start)](#3-dashboard-design-start)
4. [Inline Keyboard Navigation](#4-inline-keyboard-navigation)
5. [Launch ASM Module](#5-launch-asm-module)
6. [Position Module](#6-position-module)
7. [Trade History Module](#7-trade-history-module)
8. [Report Module](#8-report-module)
9. [Settings Module](#9-settings-module)
10. [Data Flow & API Integration](#10-data-flow--api-integration)
11. [Utility Integration](#11-utility-integration)
12. [Implementation Phases](#12-implementation-phases)

---

## 1. Telegram Bot Architecture

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM BOT LAYER                         │
│  - Command Handlers (/start, /help, /status)                │
│  - Callback Query Handlers (inline keyboard)                │
│  - Message Formatters (Markdown/HTML)                       │
│  - Session Manager (user state tracking)                    │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│              TELEGRAM SERVICE LAYER                           │
│  - DashboardService (aggregate data for dashboard)          │
│  - PositionService (get open positions)                     │
│  - TradeHistoryService (get trade history)                  │
│  - ReportService (generate shadow/live/backtest reports)    │
│  - SettingsService (manage user preferences)                │
│  - AlertService (send notifications)                        │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│              EXISTING UTILITIES (Reuse)                       │
│  - DatabaseClient (PostgreSQL/TimescaleDB)                  │
│  - ExchangeClient (Binance/Bybit API)                       │
│  - CacheClient (Redis)                                      │
│  - Logger (Loguru)                                          │
│  - ConfigManager (YAML/ENV)                                 │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ↓
┌─────────────────────────────────────────────────────────────┐
│              DATA SOURCES                                     │
│  - strategy_trades table                                    │
│  - positions table                                          │
│  - ai_decisions table                                       │
│  - statistical_features table                               │
│  - account_balance table                                    │
│  - system_health table                                      │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Technology Stack

**Telegram Bot Framework:**

- Library: `python-telegram-bot` (v20+) or `aiogram` (v3+)
- Async support: Required for non-blocking operations
- Inline keyboard: Native support
- Message formatting: MarkdownV2 or HTML

**Dependencies:**

```
python-telegram-bot==20.7
asyncio
aiohttp (for API calls)
redis (for session/cache)
loguru (for logging)
```

---

## 2. Command Structure & Handlers

### 2.1 Command List

| Command | Description | Handler Function |
| --------- | ------------- | ------------------ |
| `/start` | Show main dashboard with inline keyboard | `handle_start()` |
| `/help` | Show help message with all commands | `handle_help()` |
| `/status` | Quick system status (shortcut) | `handle_status()` |
| `/positions` | Show open positions (shortcut) | `handle_positions()` |
| `/history` | Show recent trades (shortcut) | `handle_history()` |
| `/report` | Show performance report (shortcut) | `handle_report()` |
| `/settings` | Show settings menu (shortcut) | `handle_settings()` |
| `/stop` | Emergency stop all trading | `handle_stop()` |

### 2.2 Handler Structure

**Main Handler Pattern:**

```
User sends command
    ↓
Command Handler validates user (authorization check)
    ↓
Service Layer fetches data from database/API
    ↓
Formatter Layer creates message (Markdown/HTML)
    ↓
Telegram API sends message with inline keyboard
    ↓
User clicks inline button
    ↓
Callback Query Handler processes action
    ↓
Service Layer fetches specific data
    ↓
Formatter Layer creates response message
    ↓
Telegram API updates message or sends new message
```

### 2.3 Authorization & Session Management

**User Authorization:**

- Whitelist approach: Only allow specific Telegram user IDs
- Config: `config/telegram.yaml` → `allowed_user_ids: [123456789, 987654321]`
- Check on every command/callback

**Session State:**

- Track user's current menu location
- Store in Redis with TTL (e.g., 5 minutes)
- Structure: `telegram:session:{user_id} → {current_menu: "report", last_action: timestamp}`

---

## 3. Dashboard Design (/start)

### 3.1 Dashboard Layout

**Message Structure:**

```
🤖 Karsa ASM - Hybrid Intelligence Trading System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 SYSTEM STATUS
├─ 🟢 Status: RUNNING (Shadow Mode)
├─ ⏱️ Uptime: 3d 14h 22m
├─ 🔄 Last Update: 2 minutes ago
└─ 📡 API Connection: ✅ Healthy

💰 WALLET OVERVIEW
├─ 💵 Total Balance: $10,450.32
├─ 📈 Available: $8,200.00
├─ 🔒 In Positions: $2,250.32 (21.5%)
├─ 📊 Today's PnL: +$125.50 (+1.2%)
└─ 📅 Week's PnL: +$450.20 (+4.5%)

📈 ACTIVE POSITIONS: 2/3
├─ 1. SOL/USDT LONG | +3.2% | $1,125
└─ 2. AVAX/USDT LONG | +1.8% | $1,125

🎯 TODAY'S ACTIVITY
├─ 📊 Signals Generated: 12
├─ ✅ Trades Executed: 3
├─ 🎯 Win Rate: 66.7% (2/3)
├─ 🤖 AI Confidence Avg: 78/100
└─ 🛡️ Guardrails Triggered: 2

🧠 HYBRID INTELLIGENCE
├─ 📐 Statistical Signals: 8 active
├─ 🤖 AI Evaluations: 5 today
├─ 🎯 AI Accuracy (7d): 72%
└─ 🛡️ Hard Guardrails: 10 | Soft: 5

⚠️ ALERTS
├─ 🔔 Notifications: ENABLED
├─ 📢 Last Alert: 15 min ago (SOL breakout)
└─ 🔕 Muted Until: Not muted

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use buttons below to navigate:
```

### 3.2 Dashboard Data Sources

| Section | Data Source | Query/Calculation |
| --------- | ------------- | ------------------- |
| System Status | `system_health` table | Latest health check timestamp, uptime calculation |
| Wallet Balance | Exchange API + `account_balance` table | Real-time balance from exchange |
| Active Positions | `positions` table | COUNT(*) WHERE status = 'OPEN' |
| Today's Activity | `strategy_trades` table | COUNT(*) WHERE date = today, win rate calculation |
| AI Confidence | `ai_decisions` table | AVG(confidence_score) WHERE date = today |
| Guardrails | `guardrail_logs` table | COUNT(*) WHERE date = today |
| Alerts | Redis cache | Last alert timestamp, mute status |

### 3.3 Dashboard Refresh Strategy

**Auto-Refresh:**

- Dashboard auto-refreshes every 5 minutes (if user is active)
- Manual refresh button in inline keyboard
- Cache dashboard data in Redis (TTL: 2 minutes)

**Real-Time Updates:**

- Critical alerts (trade execution, stop-loss triggered) sent immediately
- Position updates sent every 15 minutes
- Daily summary sent at 00:00 UTC

---

## 4. Inline Keyboard Navigation

### 4.1 Main Menu (After /start)

```
[🚀 Launch ASM] [📊 Positions]
[📜 Trade History] [📈 Report]
[⚙️ Settings] [🔄 Refresh Dashboard]
```

**Button Layout:**

```python
[
    [InlineKeyboardButton("🚀 Launch ASM", callback_data="launch_asm"),
     InlineKeyboardButton("📊 Positions", callback_data="positions")],
    
    [InlineKeyboardButton("📜 Trade History", callback_data="trade_history"),
     InlineKeyboardButton("📈 Report", callback_data="report")],
    
    [InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
     InlineKeyboardButton("🔄 Refresh", callback_data="refresh_dashboard")]
]
```

### 4.2 Callback Query Handler Structure

**Pattern:**

```
User clicks button
    ↓
Callback query received: callback_data = "positions"
    ↓
Router function: route_callback(callback_data)
    ↓
Switch/Case or Dictionary mapping:
    "positions" → handle_positions_callback()
    "trade_history" → handle_trade_history_callback()
    "report" → handle_report_callback()
    "settings" → handle_settings_callback()
    "launch_asm" → handle_launch_asm_callback()
    "refresh_dashboard" → handle_refresh_dashboard_callback()
    ↓
Handler function executes
    ↓
Response sent (edit message or send new message)
```

### 4.3 Navigation Flow

```
/start (Dashboard)
    ↓
[Launch ASM] → ASM Control Panel
    ↓
[Positions] → Open Positions List
    ↓
[Trade History] → Recent Trades (with pagination)
    ↓
[Report] → Report Menu
    ├─ [Shadow Funnel] → Shadow Performance
    ├─ [Live Funnel] → Live Performance
    └─ [Backtest] → Backtest Results
    ↓
[Settings] → Settings Menu
    ├─ [Risk %] → Risk Selection (10/30/50/70%)
    ├─ [Max Position] → Position Limit (3/5/7)
    └─ [Alerts] → Mute/Unmute Toggle
```

---

## 5. Launch ASM Module

### 5.1 ASM Control Panel

**Message:**

```
🚀 ASM Control Panel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current Mode: SHADOW
Status: ✅ RUNNING
Uptime: 3d 14h 22m

📊 Performance (Last 24h)
├─ Signals: 12
├─ Trades: 3
├─ Win Rate: 66.7%
└─ PnL: +$125.50

🎯 Active Strategies
├─ TrendContinuation: ✅ Active
├─ MeanReversion: ⏸️ Paused (RANGE regime)
└─ LiquidationSqueeze: ✅ Active

🧠 AI Engine
├─ Status: ✅ Connected
├─ Evaluations Today: 5
├─ Avg Confidence: 78/100
└─ API Cost Today: $0.45

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[▶️ Start Live Trading] [⏸️ Pause Trading]
[🔄 Switch to Shadow] [🔄 Switch to Live]
[🔧 Force Regime Check] [🧪 Test AI Engine]
[🔙 Back to Dashboard]
```

### 5.2 Actions

| Button | Callback Data | Action |
| -------- | -------------- | -------- |
| Start Live Trading | `asm_start_live` | Change BOT_MODE from shadow to live, confirm with user |
| Pause Trading | `asm_pause` | Set trading paused flag in Redis, stop new entries |
| Switch to Shadow | `asm_switch_shadow` | Change BOT_MODE to shadow, confirm |
| Switch to Live | `asm_switch_live` | Change BOT_MODE to live, confirm |
| Force Regime Check | `asm_force_regime` | Trigger immediate regime classification for all symbols |
| Test AI Engine | `asm_test_ai` | Send test prompt to AI, show response |
| Back to Dashboard | `back_dashboard` | Return to main dashboard |

### 5.3 Confirmation Flow

**Critical Actions (Require Confirmation):**

```
User clicks [▶️ Start Live Trading]
    ↓
Bot sends: "⚠️ Are you sure you want to start LIVE trading?\n\nThis will use REAL funds.\n\n[✅ Yes, Start Live] [❌ Cancel]"
    ↓
User clicks [✅ Yes, Start Live]
    ↓
Execute action
    ↓
Bot sends: "✅ Live trading started successfully!"
```

---

## 6. Position Module

### 6.1 Open Positions List

**Message:**

```
📊 Open Positions (2/3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣ SOL/USDT - LONG
├─ Entry: $98.50 | Current: $101.65
├─ PnL: +$31.50 (+3.2%)
├─ Size: $1,125.00 (10.8% of wallet)
├─ Stop Loss: $96.20 (Trailing)
├─ AI Confidence: 82/100
├─ Beta: 1.4 | Correlation: 0.78
├─ Opened: 2h 15m ago
└─ Strategy: TrendContinuation

2️⃣ AVAX/USDT - LONG
├─ Entry: $35.20 | Current: $35.83
├─ PnL: +$18.75 (+1.8%)
├─ Size: $1,125.00 (10.8% of wallet)
├─ Stop Loss: $34.10 (Trailing)
├─ AI Confidence: 74/100
├─ Beta: 1.2 | Correlation: 0.82
├─ Opened: 45m ago
└─ Strategy: TrendContinuation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total Unrealized PnL: +$50.25 (+2.2%)
```

**Inline Keyboard:**

```
[🔍 SOL Details] [🔍 AVAX Details]
[❌ Close SOL] [❌ Close AVAX]
[🔄 Refresh Positions] [🔙 Back]
```

### 6.2 Position Details

**Message (when clicking [🔍 SOL Details]):**

```
🔍 SOL/USDT - Position Details
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 Trade Info
├─ Side: LONG
├─ Entry Price: $98.50
├─ Current Price: $101.65
├─ Size: 11.42 SOL ($1,125.00)
├─ Entry Time: 2025-01-15 12:30 UTC
├─ Duration: 2h 15m

💰 PnL
├─ Unrealized: +$31.50 (+3.2%)
├─ Fees Paid: -$2.25
├─ Net PnL: +$29.25

🛡️ Risk Management
├─ Initial Stop: $96.20
├─ Current Trailing Stop: $97.85
├─ Stop Distance: 3.7%
├─ Position Risk: 1.0% of wallet

🧠 AI Decision
├─ Confidence: 82/100
├─ Risk Level: MEDIUM
├─ Size Recommendation: FULL
├─ Reasoning: "Strong breakout with volume confirmation. 
│              High beta but correlation is moderate."

📊 Statistical Features
├─ Beta (30d): 1.42
├─ Correlation (24h): 0.78
├─ ATR: 4.2%
├─ Volume Spike: 1.8x
├─ Distance from EMA50: +3.2%

📈 Price Action
├─ Highest Since Entry: $102.10
├─ Lowest Since Entry: $98.50
├─ Current Drawdown from High: -0.4%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[❌ Close Position] [📊 Update Stop Loss]
[🔙 Back to Positions]
```

### 6.3 Data Sources

| Field | Source | Calculation |
| ------- | -------- | ------------- |
| Entry/Current Price | `positions` table + Exchange API | Real-time price |
| PnL | Calculated | (current_price - entry_price) * position_size |
| Stop Loss | `positions` table | Latest trailing stop value |
| AI Confidence | `ai_decisions` table | confidence_score at entry time |
| Beta/Correlation | `statistical_features` table | Latest values |
| Duration | Calculated | current_time - entry_time |

---

## 7. Trade History Module

### 7.1 Recent Trades List

**Message:**

```
📜 Trade History (Last 50 Trades)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ WIN | SOL/USDT LONG
├─ Entry: $95.20 → Exit: $98.50
├─ PnL: +$33.00 (+3.5%)
├─ Duration: 4h 30m
├─ AI Confidence: 79/100
└─ Closed: 2025-01-15 10:00 UTC

❌ LOSS | ETH/USDT LONG
├─ Entry: $2,450 → Exit: $2,410
├─ PnL: -$40.00 (-1.6%)
├─ Duration: 1h 15m
├─ AI Confidence: 68/100
└─ Closed: 2025-01-15 08:30 UTC

✅ WIN | AVAX/USDT LONG
├─ Entry: $34.50 → Exit: $36.20
├─ PnL: +$17.00 (+4.9%)
├─ Duration: 6h 00m
├─ AI Confidence: 85/100
└─ Closed: 2025-01-14 22:00 UTC

❌ LOSS | LINK/USDT LONG
├─ Entry: $14.80 → Exit: $14.50
├─ PnL: -$30.00 (-2.0%)
├─ Duration: 45m
├─ AI Confidence: 62/100
└─ Closed: 2025-01-14 18:00 UTC

... (showing 4 of 50 trades)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Summary (Last 50 Trades)
├─ Win Rate: 58% (29/50)
├─ Total PnL: +$450.20
├─ Avg Win: +$35.50
├─ Avg Loss: -$22.30
├─ Profit Factor: 1.59
└─ Sharpe Ratio (30d): 1.24
```

**Inline Keyboard:**

```
[⏮️ Previous Page] [📄 Page 1/5] [⏭️ Next Page]
[📊 Full Statistics] [🔙 Back]
```

### 7.2 Full Statistics

**Message:**

```
📊 Full Trading Statistics
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 Overall Performance (All Time)
├─ Total Trades: 268
├─ Winning Trades: 156 (58.2%)
├─ Losing Trades: 112 (41.8%)
├─ Total PnL: +$4,520.50
├─ Avg Win: +$42.30
├─ Avg Loss: -$28.50
├─ Profit Factor: 1.78
└─ Expectancy: +$16.87 per trade

📈 Risk Metrics
├─ Max Drawdown: -8.5%
├─ Current Drawdown: -2.1%
├─ Sharpe Ratio (30d): 1.24
├─ Sortino Ratio (30d): 1.68
├─ Win/Loss Ratio: 1.48
└─ Risk/Reward Avg: 1:2.3

🧠 AI Performance
├─ Total AI Evaluations: 512
├─ Avg Confidence: 74/100
├─ High Confidence (>80) Win Rate: 68%
├─ Low Confidence (<60) Win Rate: 42%
├─ AI Accuracy (7d): 72%
└─ AI Cost (30d): $12.45

🛡️ Guardrail Effectiveness
├─ Hard Guardrails Triggered: 45
├─ Trades Blocked by Guardrails: 38
├─ Estimated Losses Prevented: -$1,240
├─ Soft Guardrails Triggered: 120
└─ Position Downgrades: 95

📊 Strategy Performance
├─ TrendContinuation: 180 trades, 62% win rate, +$3,200
├─ MeanReversion: 65 trades, 48% win rate, +$820
└─ LiquidationSqueeze: 23 trades, 70% win rate, +$500

📅 Time-Based Analysis
├─ Best Day: Monday (+$450)
├─ Worst Day: Friday (-$120)
├─ Best Hour (UTC): 14:00-16:00
└─ Avg Trade Duration: 3h 45m

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 7.3 Pagination Logic

**Configuration:**

- Trades per page: 10
- Total pages: ceil(total_trades / 10)
- Current page stored in Redis session

**Navigation:**

```
User clicks [⏭️ Next Page]
    ↓
Callback: trade_history_page_2
    ↓
Fetch trades 11-20 from database
    ↓
Format and send page 2
    ↓
Update keyboard: [⏮️ Previous] [📄 Page 2/5] [⏭️ Next]
```

---

## 8. Report Module

### 8.1 Report Menu

**Message:**

```
📈 Performance Reports
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Select a report to view:

🔵 Shadow Funnel - Paper trading performance
🟢 Live Funnel - Real trading performance
🟡 Backtest Results - Historical simulation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[🔵 Shadow Funnel] [🟢 Live Funnel]
[🟡 Backtest Results]
[🔙 Back to Dashboard]
```

### 8.2 Shadow Funnel Report

**Message:**

```
🔵 Shadow Funnel Report
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Overview (Last 30 Days)
├─ Mode: SHADOW (Paper Trading)
├─ Period: 2024-12-15 to 2025-01-15
├─ Starting Balance: $10,000.00
├─ Current Balance: $10,450.32
├─ Total PnL: +$450.32 (+4.5%)
└─ Status: ✅ RUNNING

🎯 Trading Activity
├─ Signals Generated: 342
├─ Trades Executed: 89
├─ Win Rate: 61.8% (55/89)
├─ Avg Trade Duration: 3h 30m
└─ Avg PnL per Trade: +$5.06

📈 Performance Metrics
├─ Profit Factor: 1.65
├─ Sharpe Ratio: 1.32
├─ Max Drawdown: -6.2%
├─ Current Drawdown: -1.8%
├─ Win/Loss Ratio: 1.52
└─ Expectancy: +$5.06 per trade

🧠 AI Engine Performance
├─ Total Evaluations: 342
├─ Avg Confidence: 76/100
├─ High Confidence (>80) Trades: 42
├─ High Confidence Win Rate: 71.4%
├─ Low Confidence (<60) Trades: 28
├─ Low Confidence Win Rate: 39.3%
├─ AI Accuracy: 74%
└─ API Cost: $8.50

🛡️ Guardrail Statistics
├─ Hard Guardrails Triggered: 15
├─ Trades Blocked: 12
├─ Estimated Losses Prevented: -$380
├─ Soft Guardrails Triggered: 45
└─ Position Downgrades: 38

📊 Strategy Breakdown
├─ TrendContinuation: 62 trades, 65% win rate, +$280
├─ MeanReversion: 20 trades, 50% win rate, +$120
└─ LiquidationSqueeze: 7 trades, 71% win rate, +$50

📅 Daily Performance (Last 7 Days)
├─ 2025-01-15: +$45.20
├─ 2025-01-14: -$12.30
├─ 2025-01-13: +$68.50
├─ 2025-01-12: +$22.10
├─ 2025-01-11: -$8.40
├─ 2025-01-10: +$55.30
└─ 2025-01-09: +$30.80

📊 Monthly Performance Chart
[ASCII chart or link to web dashboard]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[📊 Detailed Statistics] [📥 Export CSV]
[🔄 Refresh Data] [🔙 Back]
```

### 8.3 Live Funnel Report

**Message:**

```
🟢 Live Funnel Report
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ LIVE TRADING (Real Funds)
├─ Mode: LIVE
├─ Period: 2025-01-01 to 2025-01-15
├─ Starting Balance: $10,000.00
├─ Current Balance: $10,320.50
├─ Total PnL: +$320.50 (+3.2%)
├─ Realized PnL: +$280.50
├─ Unrealized PnL: +$40.00
└─ Status: ✅ RUNNING

🎯 Trading Activity
├─ Signals Generated: 156
├─ Trades Executed: 42
├─ Win Rate: 59.5% (25/42)
├─ Avg Trade Duration: 3h 15m
├─ Avg PnL per Trade: +$7.63
└─ Open Positions: 2

📈 Performance Metrics
├─ Profit Factor: 1.58
├─ Sharpe Ratio: 1.18
├─ Max Drawdown: -7.8%
├─ Current Drawdown: -2.1%
├─ Win/Loss Ratio: 1.45
└─ Expectancy: +$7.63 per trade

💰 Financial Summary
├─ Gross Profit: +$1,250.00
├─ Gross Loss: -$790.00
├─ Trading Fees: -$39.50
├─ Funding Fees: -$12.30
├─ AI API Cost: -$4.50
└─ Net Profit: +$403.70

🧠 AI Engine Performance
├─ Total Evaluations: 156
├─ Avg Confidence: 75/100
├─ High Confidence (>80) Trades: 18
├─ High Confidence Win Rate: 72.2%
├─ Low Confidence (<60) Trades: 12
├─ Low Confidence Win Rate: 41.7%
├─ AI Accuracy: 71%
└─ API Cost: $4.50

🛡️ Guardrail Statistics
├─ Hard Guardrails Triggered: 8
├─ Trades Blocked: 6
├─ Estimated Losses Prevented: -$210
├─ Soft Guardrails Triggered: 22
└─ Position Downgrades: 18

📊 Strategy Breakdown
├─ TrendContinuation: 28 trades, 64% win rate, +$210
├─ MeanReversion: 10 trades, 50% win rate, +$50
└─ LiquidationSqueeze: 4 trades, 75% win rate, +$20

⚠️ Risk Alerts
├─ Max Position Limit Hit: 3 times
├─ Correlation Block: 2 times
├─ Funding Rate Block: 1 time
└─ BTC Regime Block: 4 times

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[📊 Detailed Statistics] [📥 Export CSV]
[⚠️ Risk Report] [🔙 Back]
```

### 8.4 Backtest Results Report

**Message:**

```
🟡 Backtest Results
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Backtest Configuration
├─ Period: 2024-01-01 to 2024-12-31 (1 year)
├─ Symbols: Top 30 by volume
├─ Timeframe: 1H
├─ Initial Capital: $10,000.00
├─ Risk per Trade: 1%
├─ Max Concurrent Positions: 3
└─ Strategy: Hybrid Intelligence v2.0

🎯 Overall Performance
├─ Total Trades: 1,245
├─ Winning Trades: 712 (57.2%)
├─ Losing Trades: 533 (42.8%)
├─ Final Balance: $18,450.00
├─ Total Return: +84.5%
├─ Annualized Return: 84.5%
└─ Benchmark (BTC): +45.2%

📈 Risk-Adjusted Metrics
├─ Sharpe Ratio: 1.85
├─ Sortino Ratio: 2.42
├─ Max Drawdown: -12.3%
├─ Calmar Ratio: 6.87
├─ Profit Factor: 1.92
└─ Expectancy: +$6.78 per trade

📊 Monthly Returns
├─ Jan 2024: +8.2%
├─ Feb 2024: +5.4%
├─ Mar 2024: -3.1%
├─ Apr 2024: +12.5%
├─ May 2024: +6.8%
├─ Jun 2024: +4.2%
├─ Jul 2024: -2.8%
├─ Aug 2024: +9.1%
├─ Sep 2024: +7.3%
├─ Oct 2024: +11.2%
├─ Nov 2024: +8.9%
└─ Dec 2024: +6.8%

🧠 AI Performance (Backtest)
├─ Total Evaluations: 2,450
├─ Avg Confidence: 73/100
├─ High Confidence Win Rate: 69.5%
├─ Low Confidence Win Rate: 43.2%
├─ AI Contribution: +$2,100 (estimated)
└─ AI Cost (estimated): $45.00

🛡️ Guardrail Effectiveness
├─ Hard Guardrails Triggered: 185
├─ Trades Blocked: 142
├─ Losses Prevented: -$1,850
├─ Soft Guardrails Triggered: 420
└─ Position Downgrades: 310

📊 Strategy Comparison
├─ TrendContinuation: 820 trades, 61% win rate, +$5,200
├─ MeanReversion: 310 trades, 49% win rate, +$950
└─ LiquidationSqueeze: 115 trades, 68% win rate, +$1,100

⚠️ Backtest Limitations
├─ Slippage: Estimated 0.1% per trade
├─ No funding rate costs included
├─ No exchange downtime simulated
├─ Look-ahead bias: Minimized but not eliminated
└─ Overfitting risk: Parameters optimized on 70% of data

📈 Equity Curve
[ASCII chart or link to web dashboard]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[📊 Detailed Statistics] [📥 Export CSV]
[🔧 Run New Backtest] [🔙 Back]
```

### 8.5 Data Sources for Reports

| Report | Primary Data Source | Secondary Sources |
| -------- | --------------------- | ------------------- |
| Shadow Funnel | `shadow_trades` table | `ai_decisions`, `statistical_features` |
| Live Funnel | `strategy_trades` table | `positions`, `ai_decisions`, `guardrail_logs` |
| Backtest | `backtest_results` table | `backtest_trades`, `backtest_metrics` |

---

## 9. Settings Module

### 9.1 Settings Menu

**Message:**

```
⚙️ Trading Settings
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 Risk Management
├─ Risk per Trade: 30%
├─ Max Concurrent Positions: 3
└─ Base Risk: 1% of wallet

🔔 Notifications
├─ Trade Alerts: ✅ ENABLED
├─ Daily Summary: ✅ ENABLED
├─ Guardrail Alerts: ✅ ENABLED
└─ Mute Until: Not muted

🧠 AI Engine
├─ AI Provider: OpenAI GPT-4
├─ Temperature: 0.3
├─ Cache TTL: 4 hours
└─ Fallback: Statistical-only

🛡️ Guardrails
├─ Hard Guardrails: 10 active
├─ Soft Guardrails: 5 active
├─ Min AI Confidence: 60
└─ Max Beta in BTC Downtrend: 1.2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Inline Keyboard:**

```
[🎯 Risk %] [📊 Max Positions]
[🔔 Alert Settings] [🧠 AI Settings]
[🛡️ Guardrail Config]
[🔙 Back to Dashboard]
```

### 9.2 Risk Percentage Setting

**Message:**

```
🎯 Risk per Trade Setting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current: 30%

This determines how much of your normal position size to use:
├─ 10% = Very conservative (quarter position)
├─ 30% = Conservative (recommended)
├─ 50% = Moderate
└─ 70% = Aggressive

Example with $10,000 wallet:
├─ 10% → Max risk per trade: $30
├─ 30% → Max risk per trade: $90
├─ 50% → Max risk per trade: $150
└─ 70% → Max risk per trade: $210

Select new risk percentage:
```

**Inline Keyboard:**

```
[10%] [30%]
[50%] [70%]
[🔙 Back to Settings]
```

**Callback Handling:**

```
User clicks [50%]
    ↓
Callback: settings_risk_50
    ↓
Update Redis: settings:{user_id}:risk_pct = 50
    ↓
Update database: user_settings table
    ↓
Send confirmation: "✅ Risk per trade updated to 50%"
    ↓
Return to settings menu
```

### 9.3 Max Positions Setting

**Message:**

```
📊 Max Concurrent Positions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current: 3

This limits how many positions you can hold simultaneously:
├─ 3 = Conservative (recommended)
├─ 5 = Moderate
└─ 7 = Aggressive

Higher limit = more diversification but also more risk exposure.

Select new limit:
```

**Inline Keyboard:**

```
[3 Positions] [5 Positions]
[7 Positions]
[🔙 Back to Settings]
```

### 9.4 Alert Settings

**Message:**

```
🔔 Alert Settings
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current Status:
├─ Trade Alerts: ✅ ENABLED
├─ Daily Summary: ✅ ENABLED
├─ Guardrail Alerts: ✅ ENABLED
└─ Mute All: ❌ DISABLED

⚠️ Mute all notifications temporarily?
```

**Inline Keyboard:**

```
[🔕 Mute for 1h] [🔕 Mute for 24h]
[🔔 Unmute All]
[🔙 Back to Settings]
```

**Mute Logic:**

```
User clicks [🔕 Mute for 24h]
    ↓
Callback: settings_mute_24h
    ↓
Calculate mute_until = now + 24 hours
    ↓
Update Redis: settings:{user_id}:mute_until = mute_until
    ↓
AlertService checks mute_until before sending any alert
    ↓
Send confirmation: "✅ Notifications muted until 2025-01-16 14:00 UTC"
```

### 9.5 Settings Persistence

**Storage Strategy:**

1. **Redis (Fast Access):**
   - Key: `settings:{user_id}`
   - TTL: None (persistent)
   - Fields: `risk_pct`, `max_positions`, `mute_until`, etc.

2. **Database (Backup):**
   - Table: `user_settings`
   - Columns: `user_id`, `setting_key`, `setting_value`, `updated_at`
   - Sync Redis to DB every 5 minutes

**Default Values:**

```yaml
default_settings:
  risk_pct: 30
  max_positions: 3
  mute_until: null
  ai_provider: "openai"
  ai_temperature: 0.3
  min_ai_confidence: 60
```

---

## 10. Data Flow & API Integration

### 10.1 Data Flow Diagram

```
User sends /start
    ↓
Telegram Bot receives command
    ↓
Authorization check (whitelist)
    ↓
DashboardService.get_dashboard_data()
    ├─ SystemHealthService.get_status()
    ├─ WalletService.get_balance()
    ├─ PositionService.get_open_positions()
    ├─ TradeService.get_today_stats()
    ├─ AIService.get_ai_stats()
    └─ GuardrailService.get_guardrail_stats()
    ↓
Aggregate data into dashboard structure
    ↓
FormatterService.format_dashboard(data)
    ↓
Telegram API sends message with inline keyboard
    ↓
User clicks button (e.g., "Report")
    ↓
Callback query: "report"
    ↓
ReportService.get_report_menu()
    ↓
FormatterService.format_report_menu()
    ↓
Telegram API sends report menu
    ↓
User clicks "Shadow Funnel"
    ↓
Callback query: "report_shadow"
    ↓
ReportService.get_shadow_funnel_report()
    ├─ Query shadow_trades table
    ├─ Calculate metrics
    ├─ Query ai_decisions table
    └─ Query guardrail_logs table
    ↓
FormatterService.format_shadow_report(data)
    ↓
Telegram API sends shadow report
```

### 10.2 Service Layer Structure

**File Structure:**

```
src/telegram/
├─ bot.py (main bot initialization)
├─ handlers/
│  ├─ command_handlers.py (/start, /help, etc.)
│  ├─ callback_handlers.py (inline button clicks)
│  └─ message_handlers.py (text messages)
├─ services/
│  ├─ dashboard_service.py
│  ├─ position_service.py
│  ├─ trade_history_service.py
│  ├─ report_service.py
│  ├─ settings_service.py
│  └─ alert_service.py
├─ formatters/
│  ├─ dashboard_formatter.py
│  ├─ position_formatter.py
│  ├─ report_formatter.py
│  └─ settings_formatter.py
├─ keyboards/
│  ├─ main_keyboard.py
│  ├─ report_keyboard.py
│  └─ settings_keyboard.py
└─ utils/
   ├─ authorization.py
   ├─ session_manager.py
   └─ message_builder.py
```

### 10.3 Database Queries

**Dashboard Queries:**

```sql
-- System health
SELECT status, last_check, uptime_seconds 
FROM system_health 
ORDER BY last_check DESC LIMIT 1;

-- Open positions
SELECT symbol, side, entry_price, current_price, size, pnl_pct, opened_at 
FROM positions 
WHERE status = 'OPEN';

-- Today's stats
SELECT 
  COUNT(*) as total_trades,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
  AVG(ai_confidence) as avg_confidence
FROM strategy_trades 
WHERE DATE(closed_at) = CURRENT_DATE;

-- AI stats
SELECT 
  COUNT(*) as total_evaluations,
  AVG(confidence_score) as avg_confidence
FROM ai_decisions 
WHERE DATE(created_at) = CURRENT_DATE;
```

### 10.4 Caching Strategy

**Redis Cache Keys:**

```
telegram:dashboard:{user_id} → dashboard_data (TTL: 2 min)
telegram:positions:{user_id} → positions_list (TTL: 1 min)
telegram:settings:{user_id} → user_settings (TTL: None)
telegram:session:{user_id} → session_state (TTL: 5 min)
```

**Cache Invalidation:**

- Dashboard: Auto-refresh every 2 minutes
- Positions: Invalidate on trade execution/close
- Settings: Invalidate on setting change
- Session: Invalidate on user inactivity (5 min)

---

## 11. Utility Integration

### 11.1 Existing Utilities to Reuse

| Utility | Location | Usage in Telegram |
| --------- | ---------- | ------------------- |
| **DatabaseClient** | `src/utils/database.py` | Query trades, positions, settings |
| **ExchangeClient** | `src/utils/exchange.py` | Get real-time prices, balances |
| **CacheClient** | `src/utils/cache.py` | Redis operations for session/cache |
| **Logger** | `src/utils/logger.py` | Log all Telegram interactions |
| **ConfigManager** | `src/utils/config.py` | Load telegram.yaml config |
| **AlertService** | `src/services/alert_service.py` | Send notifications to Telegram |

### 11.2 New Utilities to Create

**Authorization Utility:**

```python
# src/telegram/utils/authorization.py

class TelegramAuthorizer:
    def __init__(self, config):
        self.allowed_user_ids = config.telegram.allowed_user_ids
    
    def is_authorized(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids
```

**Session Manager:**

```python
# src/telegram/utils/session_manager.py

class SessionManager:
    def __init__(self, cache_client):
        self.cache = cache_client
    
    def get_session(self, user_id: int) -> dict:
        key = f"telegram:session:{user_id}"
        return self.cache.get(key) or {"current_menu": "dashboard"}
    
    def set_session(self, user_id: int, session: dict):
        key = f"telegram:session:{user_id}"
        self.cache.set(key, session, ttl=300)  # 5 min TTL
```

**Message Builder:**

```python
# src/telegram/utils/message_builder.py

class MessageBuilder:
    @staticmethod
    def build_dashboard(system_status, wallet, positions, stats):
        message = "🤖 Karsa ASM - Hybrid Intelligence Trading System\n"
        message += "━" * 47 + "\n\n"
        message += "📊 SYSTEM STATUS\n"
        message += f"├─ 🟢 Status: {system_status.status}\n"
        # ... build rest of message
        return message
    
    @staticmethod
    def build_position_list(positions):
        # ... format positions
        pass
```

### 11.3 Integration with AlertService

**Existing AlertService Enhancement:**

```python
# src/services/alert_service.py

class AlertService:
    def __init__(self, telegram_bot, cache_client):
        self.bot = telegram_bot
        self.cache = cache_client
    
    async def send_alert(self, user_id: int, message: str):
        # Check if muted
        mute_until = self.cache.get(f"settings:{user_id}:mute_until")
        if mute_until and datetime.now() < mute_until:
            return  # Skip alert if muted
        
        # Send alert
        await self.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="Markdown"
        )
    
    async def send_trade_alert(self, user_id: int, trade: dict):
        message = f"✅ Trade Executed\n\n"
        message += f"Symbol: {trade['symbol']}\n"
        message += f"Side: {trade['side']}\n"
        message += f"Entry: ${trade['entry_price']}\n"
        message += f"Size: ${trade['size']}\n"
        
        await self.send_alert(user_id, message)
```

---

## 12. Implementation Phases

### Phase 1: Telegram Bot Foundation (Week 1)

**Objective:** Set up basic bot structure and /start command

**Tasks:**

1. [ ] Install dependencies (`python-telegram-bot`, etc.)
2. [ ] Create bot initialization in `src/telegram/bot.py`
3. [ ] Implement authorization utility
4. [ ] Implement session manager
5. [ ] Create `/start` command handler
6. [ ] Build basic dashboard message (static text)
7. [ ] Create main inline keyboard
8. [ ] Implement callback query router
9. [ ] Add bot token to environment variables
10. [ ] Test bot locally

**Deliverables:**

- Working Telegram bot
- /start command shows dashboard
- Inline keyboard navigation works

**Success Criteria:**

- Bot responds to /start within 2 seconds
- Authorization blocks unauthorized users
- Inline buttons trigger callbacks correctly

---

### Phase 2: Dashboard & Data Integration (Week 2)

**Objective:** Connect dashboard to real data sources

**Tasks:**

1. [ ] Implement DashboardService
2. [ ] Integrate SystemHealthService
3. [ ] Integrate WalletService (Exchange API)
4. [ ] Integrate PositionService
5. [ ] Integrate TradeService (today's stats)
6. [ ] Integrate AIService (AI stats)
7. [ ] Integrate GuardrailService
8. [ ] Implement dashboard formatter
9. [ ] Add Redis caching for dashboard
10. [ ] Implement refresh button

**Deliverables:**

- Dashboard shows real-time data
- Data refreshes automatically
- All sections populated correctly

**Success Criteria:**

- Dashboard data matches database values
- Refresh button updates data
- No performance degradation (< 3s response time)

---

### Phase 3: Position & Trade History Modules (Week 3)

**Objective:** Implement position details and trade history

**Tasks:**

1. [ ] Implement PositionService.get_open_positions()
2. [ ] Implement PositionService.get_position_details()
3. [ ] Create position list formatter
4. [ ] Create position details formatter
5. [ ] Implement position inline keyboard
6. [ ] Implement TradeHistoryService
7. [ ] Add pagination logic
8. [ ] Create trade history formatter
9. [ ] Implement full statistics view
10. [ ] Test with historical data

**Deliverables:**

- Open positions list with details
- Trade history with pagination
- Full statistics view

**Success Criteria:**

- Position details show all required fields
- Pagination works correctly
- Trade history loads within 3 seconds

---

### Phase 4: Report Module (Week 4)

**Objective:** Implement shadow, live, and backtest reports

**Tasks:**

1. [ ] Implement ReportService
2. [ ] Create shadow funnel report logic
3. [ ] Create live funnel report logic
4. [ ] Create backtest report logic
5. [ ] Implement report formatters
6. [ ] Add report inline keyboards
7. [ ] Implement CSV export (optional)
8. [ ] Add detailed statistics view
9. [ ] Test with real data
10. [ ] Optimize query performance

**Deliverables:**

- Shadow funnel report
- Live funnel report
- Backtest results report
- CSV export (optional)

**Success Criteria:**

- Reports show accurate data
- Calculations match expected values
- Reports load within 5 seconds

---

### Phase 5: Settings Module (Week 5)

**Objective:** Implement settings management

**Tasks:**

1. [ ] Implement SettingsService
2. [ ] Create risk percentage setting
3. [ ] Create max positions setting
4. [ ] Create alert mute/unmute setting
5. [ ] Implement settings formatters
6. [ ] Add settings inline keyboards
7. [ ] Implement Redis persistence
8. [ ] Implement database backup
9. [ ] Add confirmation dialogs
10. [ ] Test setting changes

**Deliverables:**

- All settings configurable
- Settings persist across restarts
- Changes take effect immediately

**Success Criteria:**

- Settings update correctly in Redis/DB
- Bot uses new settings immediately
- No data loss on restart

---

### Phase 6: Alert Integration & Polish (Week 6)

**Objective:** Integrate alerts and polish UI

**Tasks:**

1. [ ] Enhance AlertService for Telegram
2. [ ] Implement trade execution alerts
3. [ ] Implement stop-loss alerts
4. [ ] Implement guardrail alerts
5. [ ] Implement daily summary alerts
6. [ ] Add mute/unmute logic
7. [ ] Polish message formatting
8. [ ] Add error handling
9. [ ] Add loading indicators
10. [ ] Comprehensive testing

**Deliverables:**

- Real-time trade alerts
- Daily summary reports
- Mute functionality
- Polished UI

**Success Criteria:**

- Alerts sent within 5 seconds of event
- Mute functionality works correctly
- No formatting errors
- All edge cases handled

---

### Phase 7: Deployment & Monitoring (Week 7)

**Objective:** Deploy to production and monitor

**Tasks:**

1. [ ] Deploy Telegram bot to production
2. [ ] Set up monitoring (logs, errors)
3. [ ] Set up alerts for bot downtime
4. [ ] Create user documentation
5. [ ] Train user on bot usage
6. [ ] Monitor for 1 week
7. [ ] Collect feedback
8. [ ] Fix any issues
9. [ ] Optimize performance
10. [ ] Final deployment

**Deliverables:**

- Production-ready Telegram bot
- User documentation
- Monitoring system
- Stable operation

**Success Criteria:**

- Bot runs 24/7 without crashes
- Response time < 3 seconds
- No critical bugs
- User satisfied with interface

---

## Appendix A: Telegram Bot Token Setup

**Steps:**

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow prompts to name your bot
4. Receive bot token (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
5. Add to environment variables: `TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
6. Get your user ID: Send message to `@userinfobot`
7. Add your user ID to config: `allowed_user_ids: [123456789]`

---

## Appendix B: Message Formatting Examples

**MarkdownV2 Formatting:**

```python
message = """
🤖 *Karsa ASM* - Hybrid Intelligence Trading System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *SYSTEM STATUS*
├─ 🟢 Status: `RUNNING`
├─ ⏱️ Uptime: `3d 14h 22m`
└─ 🔄 Last Update: `2 minutes ago`

💰 *WALLET OVERVIEW*
├─ 💵 Total Balance: *$10,450\.32*
├─ 📈 Available: *$8,200\.00*
└─ 🔒 In Positions: *$2,250\.32* \(21\.5%\)
"""

# Escape special characters
message = escape_markdown(message)

# Send with MarkdownV2
await bot.send_message(
    chat_id=chat_id,
    text=message,
    parse_mode="MarkdownV2"
)
```

**HTML Formatting (Alternative):**

```python
message = """
<b>🤖 Karsa ASM</b> - Hybrid Intelligence Trading System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 <b>SYSTEM STATUS</b>
├─ 🟢 Status: <code>RUNNING</code>
├─ ⏱️ Uptime: <code>3d 14h 22m</code>
└─ 🔄 Last Update: <code>2 minutes ago</code>

💰 <b>WALLET OVERVIEW</b>
├─ 💵 Total Balance: <b>$10,450.32</b>
├─ 📈 Available: <b>$8,200.00</b>
└─ 🔒 In Positions: <b>$2,250.32</b> (21.5%)
"""

await bot.send_message(
    chat_id=chat_id,
    text=message,
    parse_mode="HTML"
)
```

---

## Appendix C: Error Handling

**Common Errors & Solutions:**

| Error | Cause | Solution |
|-------|-------|----------|
| `TelegramError: Unauthorized` | Invalid bot token | Check TELEGRAM_BOT_TOKEN |
| `TelegramError: Forbidden` | User blocked bot or not in whitelist | Check allowed_user_ids |
| `TelegramError: Message
