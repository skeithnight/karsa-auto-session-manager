## 🖥️ Dashboard Layout: The "Trader's Cockpit"

### **Row 1: The Financial Pulse (Wallet & PnL)**

*Goal: Immediate visibility into your money. This is the first thing you look at.*

**Panel 1.1: Wallet Overview (Stat Panel)**

- **What it shows:**
  - Total Account Equity (USD)
  - Available Balance (USD)
  - Margin Utilization (%)
- **User Education:** "If Margin Utilization is near 100%, the bot cannot take new trades. If Available Balance is dropping unexpectedly, check for hidden fees or funding rates."

**Panel 1.2: PnL Summary (Stat Panel)**

- **What it shows:**
  - **Total Realized PnL:** All-time and Today (in USD and %)
  - **Current Unrealized PnL:** Live open positions (in USD and %)
- **User Education:** "Realized is money in the bank. Unrealized is just potential. A high Unrealized PnL that never converts to Realized PnL means your Take Profit logic is too greedy."

---

### **Row 2: System Health & Performance (Is it alive and winning?)**

*Goal: Confirm the infrastructure is running and the strategy is statistically sound.*

**Panel 2.1: System Health Grid (Status Indicators)**

- **What it shows:** A clean grid of 4 critical uptime checks:
  1. **VPN / Network (Gluetun/Oxk):** 🟢 Connected (Exit IP: XX.XX.XX.XX)
  2. **Exchange API (Bybit/Binance):** 🟢 OK (Rate Limit: 45% used)
  3. **WebSocket Stream:** 🟢 OK (Lag: 12ms)
  4. **Database (Postgres/Redis):** 🟢 OK (Sync Lag: <50ms)
- **User Education:** "If *any* of these turn 🔴 Red, the bot is flying blind or cannot execute. Your first action is to check the server, not the strategy."

**Panel 2.2: Win Rate Gauge (Gauge Chart)**

- **What it shows:**
  - Main Gauge: Overall Win Rate (%)
  - Sub-text: Rolling 7-Day Win Rate (%)
- **User Education:** "If the Overall Win Rate is 60%, but the 7-Day Win Rate drops to 30%, the current market regime is hostile to your strategy. Expect a drawdown."

---

### **Row 3: Live Trading (What is happening right now)**

*Goal: Monitor active risk. Know exactly what the bot is holding.*

**Panel 3.1: Active Positions Matrix (Live Table)**

- **What it shows:** A real-time table of every open trade with these exact columns:
  - **Symbol** (e.g., BTC/USDT)
  - **Side** (LONG / SHORT)
  - **Size** (e.g., 0.5 BTC)
  - **Entry Price** vs **Current Mark Price**
  - **Unrealized PnL** ($)
  - **Stop Loss Price** (and % distance to current price)
  - **Time in Trade** (e.g., 2h 15m)
- **User Education:** "Scan the 'Distance to Stop Loss'. If price is rapidly approaching the SL, the trade is being tested. If 'Time in Trade' is abnormally long, the bot might be stuck in a ranging market."

---

### **Row 4: Trade History (The Audit Trail)**

*Goal: Review recent execution quality and verify the bot closed trades correctly.*

**Panel 4.1: Recent Closed Trades (Scrollable Table)**

- **What it shows:** The last 15-20 completed trades with these columns:
  - **Close Time** (UTC)
  - **Symbol & Side**
  - **Entry Price** → **Exit Price**
  - **Realized PnL** ($) and (%)
  - **Duration** (e.g., 45m)
  - **Exit Reason** (e.g., "Take Profit Hit", "Stop Loss Hit", "Time Stop", "Emergency Flatten")
- **User Education:** "Look at the 'Exit Reason'. If you see too many 'Stop Loss Hit' or 'Time Stop' in a row, your Alpha Bridge is generating false signals in the current market. If you see 'Slippage' eating into the PnL, your execution logic needs tuning."

---

## ⚙️ Dashboard Configuration Rules (For Setup)

To make this dashboard actually useful, enforce these rules when building it in Grafana:

1. **Auto-Refresh Rate:** Set to **10 to 15 seconds**. Fast enough to see live PnL and position changes, but slow enough to not overwhelm your browser or the database.
2. **Color Coding (Strict):**
   - 🟢 **Green:** Positive PnL, Healthy Systems, Long positions (optional, but standard).
   - 🔴 **Red:** Negative PnL, System Failures, Short positions (optional).
   - 🟡 **Yellow:** Warnings (e.g., Margin usage > 80%, API rate limit > 75%).
3. **Time Range Default:** Set the default time picker to **"Last 24 Hours"** or **"Last 7 Days"**. This keeps the Trade History and Win Rate panels focused on recent, relevant performance.
4. **No Clutter:** Do not add complex Sankey diagrams, Alpha score distributions, or regression lines to this specific dashboard. If you want to debug *why* a trade happened, you switch to the Funnel Dashboard. This dashboard is strictly for **monitoring**.

---

## 🚨 The 10-Second Daily Check Protocol

When you open this dashboard, follow this exact 10-second scan:

1. **Seconds 0-3 (Row 2):** Are all 4 System Health lights 🟢 Green? *(If no, investigate infrastructure immediately).*
2. **Seconds 3-5 (Row 1):** Is Total Equity stable or growing? Is Unrealized PnL within normal bounds? *(If Unrealized is deeply negative, check Row 3).*
3. **Seconds 5-7 (Row 3):** Look at Active Positions. Are Stop Losses in place? Is the bot holding anything unexpectedly?
4. **Seconds 7-10 (Row 4):** Glance at the last 3 rows of Trade History. Are they mostly green (TP hits), or is there a cluster of red (SL hits)?
