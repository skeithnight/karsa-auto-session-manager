## 🎯 SHADOW DASHBOARD DESIGN (The "Flight Simulator")

### **Dashboard Philosophy**

The Shadow Dashboard is the critical bridge between historical theory (Backtest) and real-world execution (Live). Its purpose is not just to show "fake profits," but to **stress-test the bot’s infrastructure, execution logic, and risk gates against live market friction**.

The ultimate question this dashboard answers is: *"Does the bot behave in the live market exactly as the backtest predicted, and can the infrastructure handle the load without errors?"*

---

### **Row 1: Executive Summary (The "Simulation Health" Row)**

**Panel 1.1: Shadow Scorecard (Stat Panel)**

- **What it shows:**
  - Simulated Net PnL (%)
  - Simulated Win Rate (%)
  - Max Simulated Drawdown (%)
  - Total Simulated Trades
  - Days in Shadow Mode
- **User Education:**
  - *Rule of Thumb:* "Treat simulated capital exactly as you would real capital. If you wouldn't deploy this live, do not ignore the red metrics here."
  - "A high win rate with negative PnL means your losers are too big. Check your Risk Gate."

**Panel 1.2: Shadow vs. Backtest Equity Divergence (Time Series)**

- **What it shows:**
  - Line 1: Shadow Equity Curve (starting at simulated $10,000)
  - Line 2: Backtest Equity Curve for the *exact same calendar dates*
  - Shaded Area: The divergence gap between the two.
- **User Education:**
  - "This is the ultimate reality check. If Shadow is significantly underperforming the Backtest for the same period, your backtest was overfitted, or market conditions have shifted."
  - "A divergence of >5% requires immediate investigation."

**Panel 1.3: Signal-to-Trade Conversion Rate (Gauge)**

- **What it shows:** Percentage of Alpha Bridge signals that successfully became executed trades.
- **User Education:**
  - "If this is low (e.g., < 60%), your Risk Gate is too strict, or your API is rejecting orders. You are missing opportunities."
  - "If this is 100%, your Risk Gate might be too loose. Verify slippage tolerances."

---

### **Row 2: Execution Friction (The "Reality Check" Row)**

*This is the most important row in the Shadow Dashboard. Backtests assume perfect fills; Shadow proves otherwise.*

**Panel 2.1: Simulated Slippage Distribution (Histogram)**

- **What it shows:** The difference (in basis points) between the signal's intended entry price and the simulated fill price.
- **User Education:**
  - "This shows the true cost of market impact. A wide spread to the right means you are getting poor fills."
  - "If average slippage exceeds your backtest assumption, you must widen your slippage tolerance or reduce position size."

**Panel 2.2: Risk Gate Rejection Reasons (Pie Chart)**

- **What it shows:** Breakdown of why signals were blocked during Shadow mode (e.g., "Max Positions Reached", "Slippage Exceeded", "Circuit Breaker Open").
- **User Education:**
  - "This tells you *why* the bot is saying 'no'. If 'Slippage Exceeded' is the top reason, your entry logic is chasing the market."

**Panel 2.3: API Latency & Order Acknowledgment Time (Time Series)**

- **What it shows:** Milliseconds taken from "Signal Generated" to "Order Confilled by Exchange".
- **User Education:**
  - "High latency kills micro-structure strategies. If this spikes above 500ms, your network or the exchange is congested."

---

### **Row 3: Real-Time Trade Lifecycle (The "Micro" Row)**

**Panel 3.1: Active Shadow Positions (Table)**

- **What it shows:** Live list of open simulated trades with:
  - Symbol & Side
  - Entry Price & Current Mark Price
  - Unrealized PnL ($) and (%)
  - Time in Trade (e.g., "2h 15m")
  - Current Stop Loss / Take Profit levels
- **User Education:**
  - "Monitor how long trades sit unrealized. If 'Time in Trade' consistently exceeds your backtest average, the market is ignoring your signals."

**Panel 3.2: Trade PnL Progression (Sparklines)**

- **What it shows:** Mini line charts for the last 5 closed shadow trades, showing the PnL trajectory from entry to exit.
- **User Education:**
  - "Do your trades go green quickly and stay green? Or do they whip back and forth? Smooth trajectories indicate good entry timing."

---

### **Row 4: Alpha Decay & Regime Validation (The "Model Drift" Row)**

**Panel 4.1: Rolling Win Rate (7-Day) (Time Series)**

- **What it shows:** A 7-day moving average of the shadow win rate.
- **User Education:**
  - "Strategies decay. If the 7-day rolling win rate drops below your backtest baseline for more than 5 days, the market regime has changed."

**Panel 4.2: Performance by Time of Day (Heatmap)**

- **What it shows:** PnL generated during different UTC hours (e.g., Asian Session, London Open, NY Close).
- **User Education:**
  - "If your strategy only makes money during the NY session but loses during Asia, you should add a time-based filter to the Strategy Router to save fees."

---

### **Row 5: Infrastructure & System Health (The "Plumbing" Row)**

*Shadow mode is the safest place to find code bugs and infrastructure weaknesses.*

**Panel 5.1: Websocket Disconnects & Reconnects (Bar Chart)**

- **What it shows:** Count of WS drops per day.
- **User Education:**
  - "Frequent drops mean your bot is flying blind. If this is >2 per day, check your `gluetun` VPN stability or server resources."

**Panel 5.2: Database Sync Lag (Time Series)**

- **What it shows:** Millisecond delay between an exchange event and the PostgreSQL write.
- **User Education:**
  - "If this spikes, your database is bottlenecking. A lagging database leads to 'Ghost Positions' on restart."

**Panel 5.3: Rate Limit Headroom (Gauge)**

- **What it shows:** Percentage of Bybit API weight remaining.
- **User Education:**
  - "If this consistently drops below 20%, your bot is polling too aggressively and risks a temporary IP ban."

---

### **Row 6: The "Go/No-Go" to Live Decision (The Action Row)**

**Panel 6.1: Shadow Graduation Checklist (Table with Dynamic Checkmarks)**

- **What it shows:**
  - [✓] Minimum 50 trades executed (Statistical significance)
  - [✓] Minimum 14 days in Shadow mode (Covers multiple market regimes)
  - [✓] Shadow Win Rate within 5% of Backtest Win Rate
  - [✓] Max Simulated Drawdown < Backtest Max Drawdown
  - [✓] Zero "Critical" infrastructure errors (e.g., unhandled exceptions, DB sync failures)
  - [✓] Slippage is within acceptable backtest assumptions
- **User Education:**
  - "Do not skip steps. All boxes must be checked to graduate to Live mode."
  - "Failing the 'Minimum Trades' check means your results are just luck, not edge."

**Panel 6.2: Recommended Next Steps (Dynamic Text Panel)**

- **What it shows:** Context-aware guidance based on Shadow performance.
  - *If all metrics pass:* "✅ Shadow validation successful. Strategy is ready for Live deployment with initial small sizing."
  - *If slippage is high:* "⚠️ Execution friction is too high. Switch from Market to Limit entries, or reduce position size."
  - *If win rate is low:* "❌ Market regime has shifted. Return to Backtest Dashboard, adjust Alpha Bridge parameters, and restart Shadow."
  - *If infrastructure errors > 0:* "🛑 Fix code/infrastructure bugs before risking real capital."

---

## 📚 USER EDUCATION: THE 5-MINUTE SHADOW VALIDATION PROTOCOL

When reviewing the Shadow Dashboard, users must follow this strict sequence to avoid "Paper Trading Syndrome" (ignoring fake losses):

**Minute 1: The Sanity Check (Row 1)**

- Look at the Shadow vs. Backtest Divergence.
- Ask: "Is the shadow equity tracking closely with the backtest equity for this period?"
- If NO → The model is broken or the market changed. Halt and investigate.
- If YES → Continue.

**Minute 2: The Friction Check (Row 2)**

- Look at Simulated Slippage and Risk Gate Rejections.
- Ask: "Is the bot getting reasonable fills, or is it constantly chasing the market?"
- If fills are poor → Adjust entry logic or slippage tolerance.

**Minute 3: The Behavior Check (Row 3)**

- Look at Active Positions and Time in Trade.
- Ask: "Are trades behaving as expected? Are Stop Losses and Take Profits triggering correctly?"
- If trades are stuck or ignoring exits → Check the Post-Execution Management logic.

**Minute 4: The Infrastructure Check (Row 5)**

- Look at Websocket drops and Rate Limit Headroom.
- Ask: "Is the bot's 'plumbing' stable, or is it struggling to keep up?"
- If unstable → Fix server/network issues before going live.

**Minute 5: The Graduation Decision (Row 6)**

- Look at the Shadow Graduation Checklist.
- Count checkmarks.
  - 6/6 → ✅ **APPROVED FOR LIVE** (Start with 25% of intended capital).
  - 4-5/6 → ⚠️ **EXTEND SHADOW** (Run for 7 more days to gather more data).
  - <4/6 → ❌ **REJECT & RECALIBRATE** (Return to strategy development).

---

## 🎨 DASHBOARD LAYOUT SPECIFICATIONS

### **Visual Design Principles**

1. **Color Coding:**
   - **Blue:** Simulated/Shadow metrics (to visually distinguish from Live Green/Red).
   - **Green:** Positive alignment with backtest expectations.
   - **Red:** Divergence from backtest, infrastructure errors, or excessive slippage.
   - **Yellow:** Warnings (e.g., rate limits approaching, rolling win rate dipping).

2. **Time Range Selector:**
   - Default: "Since Shadow Mode Started"
   - Quick filters: "Last 24h", "Last 7 Days", "Last 30 Days"
   - *Note:* Unlike Backtest, Shadow dashboard should auto-refresh every 15-30 seconds to reflect live simulation state.

3. **Alerting Integration:**
   - Shadow dashboard should trigger real alerts (Telegram/Discord) for:
     - Simulated Max Drawdown breach.
     - Websocket disconnects > 3 in 1 hour.
     - Risk Gate rejecting > 80% of signals for 1 hour.

---

## 🔄 DASHBOARD NAVIGATION FLOW

```
Backtest Dashboard (Historical Validation)
    ↓ (if validated)
Shadow Dashboard (Real-Time Simulation & Stress Test)
    ↓ (if Graduation Checklist is 6/6)
Live Dashboard (Real Capital Execution)
    ↓ (if Live Drawdown > Threshold OR Live Diverges from Shadow)
← ← ← Return to Shadow/Backtest for immediate recalibration
```

---

## ✅ SHADOW DASHBOARD SUCCESS CRITERIA

A user should be able to answer these questions within 5 minutes of viewing the dashboard:

1. **Fidelity:** "Is the bot executing in real-time exactly as the backtest predicted it would?"
2. **Friction:** "Are slippage, fees, and latency eating into my theoretical edge?"
3. **Stability:** "Is the infrastructure (API, DB, Websockets) holding up under live load without errors?"
4. **Risk:** "Is the Risk Gate correctly blocking bad trades without blocking good ones?"
5. **Action:** "Have I gathered enough statistically significant data (trades + time) to confidently deploy real capital?"
