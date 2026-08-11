# Telegram Bot Interface Specification
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Overview & Architecture

The Telegram Bot interface serves as the primary operator control panel and alert center.

### Single-Owner Polling Architecture
To prevent Telegram API long-polling collisions (`Conflict: terminated by other long poll`), polling is assigned **exclusively** to the `karsa-commander` container (`KARSA_ROLE=commander`). Live trading loops (`karsa-live`) perform execution and push notifications via `AlertService` without starting polling updaters.

---

## 2. Command Reference

| Command | Action | Description |
| :--- | :--- | :--- |
| `/start` | Launch Dashboard | Displays system health, wallet balance, active session status, and navigation keyboard. |
| `/dashboard` | View Dashboard | Edit-in-place refresh of system dashboard metrics. |
| `/portfolio` | Portfolio Summary | Shows active open positions, entry prices, leverage, PnL, and Stop Loss levels. |
| `/performance` | Performance Stats | Displays win rate, total PnL, profit factor, Sharpe ratio, and drawdown. |
| `/control` | Control Panel | Provides session control, manual halt, and kill switch triggers. |
| `/settings` | Settings Menu | View and adjust system risk limits, sector caps, and trade parameters. |
| `/history` | Trade History | Browse historical executed trade logs and PnL breakdowns. |
| `/analytics` | Decision Intelligence | View AI effectiveness win rates and trade lifecycle metrics (MAE, MFE, peak R). |
| `/summary` | Daily Summary | Generate comprehensive daily PnL and AI evaluation summaries. |

---

## 3. System Dashboard Display

```text
🤖 KARSA AUTO SESSION MANAGER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
System Health
DB 🟢   Redis 🟢   Bybit 🟢   VPN 🟢

Wallet
Balance   $ 105.79
Available $  82.53
Deployed  $  23.26  [███░░░░░░░░░] 22.0%

Session   🟢 ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[🚀 Launch Session]  [📊 Dashboard]
[💼 Portfolio]       [🎛️ Control]
[⚙️ Settings]        [📜 History]
```

---

## 4. Proactive Alert Service (`AlertService`)

- **Trade Fills**: Sends immediate notifications upon order fills with entry price, size, side, and exchange-side Stop Loss confirmation.
- **Risk & Circuit Breaker**: Pushes alerts if daily drawdown limits, consecutive loss triggers, or sector caps fire.
- **Health Degraded**: Sends warning alerts if WebSocket feeds or API connections drop.
