## 🎯 LIVE DASHBOARD DESIGN (The "Command Center")

### **Dashboard Philosophy**

The Live Dashboard is not for strategy research; it is a **mission-critical control panel**. Its sole purpose is to answer three questions instantly:

1. Is my capital safe?
2. Is the infrastructure functioning perfectly?
3. Do I need to intervene *right now*?

Speed of comprehension is everything. Under stress, a user must be able to assess the bot's health in under 10 seconds.

---

### **Row 1: Capital Protection & Executive Summary (The "Pulse")**

**Panel 1.1: Live Equity & Daily PnL (Stat Panel)**

- **What it shows:**
  - Current Total Equity (USD)
  - Daily Realized PnL ($) and (%)
  - Daily Unrealized PnL ($) and (%)
  - Current Drawdown from All-Time High (%)
- **User Education:**
  - "This is your bottom line. Unrealized PnL is hope; Realized PnL is reality."
  - "If Daily Drawdown approaches your hard-stop limit (e.g., 5%), prepare for automatic system halt."

**Panel 1.2: Active Exposure Gauge**

- **What it shows:**
  - Total Margin Utilized (%)
  - Number of Open Positions (e.g., 2/3 Max Allowed)
  - Net Portfolio Delta (e.g., +0.8 = Net Long, -0.8 = Net Short)
- **User Education:**
  - "This shows your current market risk. A Net Delta near 0 means you are market-neutral. High utilization means less room for new signals."

---

### **Row 2: System Health & Infrastructure (The "Plumbing")**

*This is the most critical row for preventing catastrophic, silent failures. It monitors the exact stack you specified.*

**Panel 2.1: VPN & Network Tunnel Health (Gluetun / Oxk / WireGuard)**

- **What it shows:**
  - Tunnel Status: 🟢 CONNECTED / 🔴 DISCONNECTED
  - Current Exit IP Address (with geo-location flag)
  - Tunnel Latency to Exchange (ms)
  - Data throughput (MB sent/received)
- **User Education:**
  - "If this turns RED, your bot's IP has reverted to your host server's IP, exposing you to ISP throttling, IP bans, or geographic restrictions. Trading must halt immediately."

**Panel 2.2: Exchange API & WebSocket Health (Multi-Endpoint)**

- **What it shows:** A grid of connection statuses:
  - **Bybit Mainnet (REST):** 🟢 OK | Last Success: 2s ago | Rate Limit Used: 45%
  - **Bybit Mainnet (WebSocket):** 🟢 OK | Heartbeat Lag: 12ms | Reconnects (24h): 0
  - **Binance (REST/WS):** 🟢 OK | Last Success: 1s ago | Rate Limit Used: 30%
  - **CCXT Manager Layer:** 🟢 OK | Active Instances: 2
- **User Education:**
  - "Green means the bot can see the market and execute. Yellow means rate limits are approaching (>80%). Red means the bot is blind or cannot trade. A WebSocket lag > 500ms indicates severe network degradation."

**Panel 2.3: Internal Services Health (Database & Core)**

- **What it shows:**
  - PostgreSQL: 🟢 Connected | Sync Lag: < 50ms
  - Redis: 🟢 Connected | Memory Usage: 120MB
  - Alpha Bridge / Strategy Router: 🟢 Running (Uptime: 14d 2h)
  - System Watchdog: 🟢 Active (Last Ping: 5s ago)
- **User Education:**
  - "If Redis or Postgres goes red, the bot is trading with amnesia. A crash in this state will result in 'Ghost Positions'."

---

### **Row 3: Real-Time Trade Lifecycle (The "Action")**

**Panel 3.1: Active Positions Matrix (Live Table)**

- **What it shows:** Real-time list of every open trade:
  - Symbol | Side | Size | Entry Price | Current Mark Price
  - Unrealized PnL ($) | Distance to Stop Loss (%)
  - Time in Trade | Associated Strategy/Session
- **User Education:**
  - "Monitor the 'Distance to Stop Loss'. If price is rapidly approaching this number, the Risk Gate's defensive post-execution logic is being tested."

**Panel 3.2: Recent Trade Tape (Scrolling Log)**

- **What it shows:** The last 10 executed actions (Fills, SL triggers, TP triggers, Cancellations) with timestamps.
- **User Education:**
  - "This is your audit trail. If you see unexpected 'Market Close' actions, the System Watchdog or Circuit Breaker intervened."

---

### **Row 4: Anomaly Detection & Execution Friction (The "Shield")**

**Panel 4.1: Live Slippage Tracker (Scatter Plot)**

- **What it shows:** Difference between the Alpha Bridge's intended price and the actual Bybit/Binance fill price for the last 24 hours.
- **User Education:**
  - "Consistent negative slippage (getting worse fills than expected) means your strategy's edge is being eaten by market impact or latency."

**Panel 4.2: Risk Gate Rejection Counter (Time Series)**

- **What it shows:** Number of signals blocked by the Risk Gate per hour, categorized by reason (e.g., "Max Positions", "Slippage Limit", "Drawdown Limit").
- **User Education:**
  - "A sudden spike in rejections means market volatility has triggered your safety protocols. This is the system working *as intended* to protect capital."

**Panel 4.3: Desync Alert Monitor (Status Panel)**

- **What it shows:** Result of the continuous 60-second reconciliation check between Local DB and Exchange.
  - Status: 🟢 SYNCED | Last Checked: 15s ago | Divergence: 0.00%
- **User Education:**
  - "If this turns RED, the bot's internal state no longer matches the exchange. All trading halts automatically until manual reconciliation is performed."

---

### **Row 5: Human-in-the-Loop (HITL) & Emergency Controls (The "Kill Switch")**

*This row is for manual override. In a crisis, the user must not have to dig through code or SSH terminals.*

**Panel 5.1: Emergency Action Center (Button Grid)**

- **What it shows:** High-friction, confirmation-required action buttons:
  - 🛑 **GLOBAL HALT:** Stops all new signals and cancels all open limit orders. (Requires 2-step confirmation).
  - 🚨 **EMERGENCY FLATTEN:** Market-closes ALL open positions immediately, regardless of PnL. (Requires typing "CONFIRM FLATTEN").
  - 🔄 **FORCE RECONCILIATION:** Manually triggers the 5-Phase Startup Reconciliation sequence without restarting the bot.
- **User Education:**
  - "Use GLOBAL HALT if you suspect a bug or extreme market news. Use EMERGENCY FLATTEN only if the bot is actively malfunctioning and losing capital."

**Panel 5.2: Manual Intervention Audit Log (Table)**

- **What it shows:** A log of every manual action taken via the dashboard (e.g., "User X triggered Emergency Flatten at 14:32 UTC").
- **User Education:**
  - "This ensures accountability. Every manual override is permanently recorded for post-mortem analysis."

---

## 📚 USER EDUCATION: THE 60-SECOND LIVE TRIAGE PROTOCOL

When opening the Live Dashboard, especially after receiving an alert, the user must follow this strict, rapid sequence:

**Seconds 0-10: The Infrastructure Check (Row 2)**

- Look at the VPN (Oxk/Gluetun) and Exchange (Bybit Mainnet/Binance) panels.
- *Question:* "Are all lights green? Is the WebSocket lag under 100ms?"
- *Action:* If RED, hit **GLOBAL HALT** immediately. Do not trust any trading logic while disconnected.

**Seconds 10-20: The Capital Check (Row 1)**

- Look at Daily PnL and Current Drawdown.
- *Question:* "Is the drawdown approaching the hard-stop limit? Is Unrealized PnL bleeding rapidly?"
- *Action:* If bleeding faster than the strategy's normal volatility, prepare to **EMERGENCY FLATTEN**.

**Seconds 20-30: The State Check (Row 4)**

- Look at the Desync Alert Monitor.
- *Question:* "Is the bot synced with the exchange?"
- *Action:* If RED, hit **GLOBAL HALT**, then **FORCE RECONCILIATION**. Do not trade until synced.

**Seconds 30-60: The Position Check (Row 3)**

- Look at the Active Positions Matrix.
- *Question:* "Are Stop Losses in place? Is the exposure within normal limits?"
- *Action:* If a position is missing its SL or is abnormally large, manually intervene or adjust the Risk Gate parameters.

---

## 🎨 DASHBOARD LAYOUT & ALERTING SPECIFICATIONS

### **Visual Design Principles**

1. **Color Coding (Strict):**
   - 🟢 **Green:** Normal, healthy, within parameters.
   - 🟡 **Yellow:** Warning (e.g., Rate limit > 75%, Slippage slightly elevated, Drawdown > 50% of max limit).
   - 🔴 **Red:** Critical failure (e.g., VPN down, WebSocket disconnected, Desync detected, Drawdown limit breached).
   - ⚪ **Gray:** Neutral/Informational (e.g., Uptime counters).

2. **Refresh Rate:**
   - **Auto-refresh:** Every 3 to 5 seconds for Price, PnL, and Active Positions.
   - **Auto-refresh:** Every 15 seconds for System Health and Rate Limits.
   - *Note:* The dashboard must remain responsive even if the backend is under heavy load.

3. **Escalating Alerting (Telegram/Discord/PagerDuty):**
   - **Level 1 (Info):** Trade filled, daily PnL target reached.
   - **Level 2 (Warning):** WebSocket reconnect, Rate limit > 80%, Slippage > 0.5%.
   - **Level 3 (Critical - Page the Human):** VPN disconnected, Desync detected, Daily Drawdown > 80% of limit, 3 consecutive failed API calls.

---

## ✅ LIVE DASHBOARD SUCCESS CRITERIA

A user should be able to answer these questions within **60 seconds** of opening the dashboard, even if they are waking up at 3 AM to an alert:

1. **Safety:** "Is my money currently safe, or is it actively bleeding?"
2. **Connectivity:** "Is the bot actually connected to Bybit Mainnet/Binance through the secure VPN, or is it flying blind?"
3. **Integrity:** "Does the bot know what positions it actually holds, or is it hallucinating?"
4. **Control:** "If everything is going wrong, do I have a single, obvious button to stop the bleeding immediately?"
