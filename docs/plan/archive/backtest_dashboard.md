## Dashboard Architecture Overview

| Dashboard | Purpose | Data Source | Time Range | User Action |
|-----------|---------|-------------|------------|-------------|
| **Live** | Real-time execution monitoring | Live bot metrics + Exchange API | Last 24h - 7d | Intervention, emergency halt |
| **Shadow** | Paper trading validation | Simulated execution engine | Last 7d - 30d | Strategy tuning, Risk Gate calibration |
| **Backtest** | Historical strategy validation | Historical replay engine | Custom date ranges | Go/No-Go deployment decisions |

---

## 🎯 BACKTEST DASHBOARD DESIGN (Primary Focus)

### **Dashboard Philosophy**

The Backtest Dashboard is not just a results viewer—it is a **strategy validation laboratory**. Users must be able to answer: *"Would I have trusted this strategy with real capital during this historical period?"*

---

### **Row 1: Executive Summary (The "Go/No-Go" Row)**

**Panel 1.1: Strategy Scorecard (Stat Panel)**

- **What it shows:**
  - Total Return (%)
  - Sharpe Ratio
  - Max Drawdown (%)
  - Total Trades
  - Win Rate (%)
  - Profit Factor
- **User Education:**
  - *Green highlight* if Sharpe > 1.5, Win Rate > 45%, Max DD < 15%
  - *Red highlight* if any metric fails minimum thresholds
  - Tooltip explains: "These are your strategy's vital signs. All must be green to proceed."

**Panel 1.2: Equity Curve Comparison (Time Series)**

- **What it shows:**
  - Line 1: Strategy Equity (starting at $10,000)
  - Line 2: Buy & Hold Benchmark (same starting capital)
  - Line 3: Maximum Drawdown Watermark (shaded area)
- **User Education:**
  - "If your strategy line doesn't consistently beat Buy & Hold, why trade?"
  - "The shaded red area shows when you would have been underwater. Can you stomach this?"

**Panel 1.3: Risk-Adjusted Returns Gauge**

- **What it shows:**
  - Sortino Ratio (gauge 0-3)
  - Calmar Ratio (gauge 0-5)
  - Omega Ratio (gauge 0-3)
- **User Education:**
  - "These metrics tell you if returns are worth the risk. Higher is better."
  - "Sortino > 2.0 means excellent downside protection."

---

### **Row 2: Drawdown & Risk Analysis (The "Pain Tolerance" Row)**

**Panel 2.1: Underwater Curve (Area Chart)**

- **What it shows:** Percentage drawdown from peak equity over time
- **User Education:**
  - "This shows your maximum pain at any point. The deepest point is your Max Drawdown."
  - "Ask yourself: Would I have quit at -20%? If yes, this strategy is too risky for you."

**Panel 2.2: Drawdown Duration Distribution (Histogram)**

- **What it shows:** How long (in days) the strategy stayed in drawdown
  - Bar 1: 0-3 days
  - Bar 2: 4-7 days  
  - Bar 3: 8-14 days
  - Bar 4: 15+ days
- **User Education:**
  - "Even profitable strategies have losing periods. This shows how long you'd wait for recovery."
  - "If most bars are in 15+ days, you need serious patience."

**Panel 2.3: Rolling Sharpe Ratio (Time Series)**

- **What it shows:** 30-day rolling Sharpe Ratio
- **User Education:**
  - "Strategies decay. This shows when your edge was strong (above 1.5) vs weak (below 0.5)."
  - "Long periods below 0.5 mean the strategy stopped working. Would you have noticed?"

---

### **Row 3: Trade Quality & Execution (The "Reality Check" Row)**

**Panel 3.1: Win Rate & Profit Factor by Month (Heatmap)**

- **What it shows:**
  - X-axis: Months
  - Y-axis: Metrics (Win Rate, Profit Factor, Avg Trade PnL)
  - Color: Green (good) to Red (bad)
- **User Education:**
  - "No strategy wins every month. This shows consistency."
  - "Red streaks of 3+ months mean you'd be questioning the strategy. Could you hold?"

**Panel 3.2: Trade Duration vs Profitability (Scatter Plot)**

- **What it shows:**
  - X-axis: Trade Duration (hours)
  - Y-axis: PnL (%)
  - Color: Green (winner) / Red (loser)
- **User Education:**
  - "Do your winners run longer than losers? They should."
  - "If losers cluster on the right (long duration), you're holding losing trades too long."

**Panel 3.3: Slippage & Fee Impact Analysis (Bar Chart)**

- **What it shows:**
  - Bar 1: Gross PnL (before costs)
  - Bar 2: After Fees
  - Bar 3: After Slippage
  - Bar 4: Net PnL (final)
- **User Education:**
  - "This is where theoretical alpha dies. Watch how much disappears."
  - "If fees + slippage consume >30% of gross PnL, your strategy is too high-frequency."

---

### **Row 4: Market Regime Performance (The "Context" Row)**

**Panel 4.1: Performance by Volatility Regime (Pie/Donut Chart)**

- **What it shows:**
  - Slice 1: Low Volatility (ATR < 1%)
  - Slice 2: Medium Volatility (ATR 1-3%)
  - Slice 3: High Volatility (ATR > 3%)
  - Size: % of total PnL from each regime
- **User Education:**
  - "Does your strategy only work in one market condition? That's dangerous."
  - "If 80% of profits come from High Vol, you'll bleed in calm markets."

**Panel 4.2: Bull vs Bear Market Performance (Side-by-Side Bars)**

- **What it shows:**
  - Left Bar: Total PnL in Bull Markets (BTC/ETH rising)
  - Right Bar: Total PnL in Bear Markets (BTC/ETH falling)
- **User Education:**
  - "A robust strategy works in both directions. One-sided strategies get destroyed in regime shifts."

**Panel 4.3: Correlation to BTC/ETH (Time Series)**

- **What it shows:**
  - 30-day rolling correlation coefficient (-1 to +1)
- **User Education:**
  - "If correlation is always +0.8, you're just holding BTC with extra steps."
  - "Negative correlation periods show true alpha. Look for variety."

---

### **Row 5: Position Sizing & Risk (The "Capital Efficiency" Row)**

**Panel 5.1: Distribution of Position Sizes (Histogram)**

- **What it shows:** How often each position size was used (as % of portfolio)
- **User Education:**
  - "Consistent sizing = consistent risk. Wild variation means undisciplined execution."

**Panel 5.2: Largest Winners vs Largest Losers (Table)**

- **What it shows:** Top 5 winning trades and Top 5 losing trades with:
  - Symbol
  - Entry/Exit Date
  - PnL %
  - Hold Time
- **User Education:**
  - "Are your winners 2-3x bigger than losers? They should be."
  - "If losers are similar size to winners, your risk management is broken."

**Panel 5.3: Capital Utilization Over Time (Area Chart)**

- **What it shows:** % of available capital deployed at any time
- **User Education:**
  - "100% utilization = maximum risk. 0% = missing opportunities."
  - "Healthy strategies vary between 30-70% based on opportunity quality."

---

### **Row 6: Strategy Decay & Robustness (The "Future-Proofing" Row)**

**Panel 6.1: Cumulative Trade Count vs Cumulative PnL (Dual Axis)**

- **What it shows:**
  - Line 1: Total trades (increasing)
  - Line 2: Cumulative PnL
- **User Education:**
  - "If PnL flattens while trades increase, the strategy is decaying."
  - "You want both lines rising together."

**Panel 6.2: Monthly PnL Distribution (Box Plot)**

- **What it shows:** Statistical distribution of monthly returns
  - Box: 25th-75th percentile
  - Line: Median
  - Whiskers: Min/Max
- **User Education:**
  - "Tight box = consistent returns. Tall box = volatile returns."
  - "Median should be positive. If it's negative, most months lose money even if total is positive."

**Panel 6.3: Strategy "Death Zone" Identifier (Annotation Overlay)**

- **What it shows:** Periods where the strategy would have been halted by your Risk Gate rules
- **User Education:**
  - "These red zones show when your own rules would have stopped the strategy."
  - "If death zones cover 40%+ of the backtest, your Risk Gate is too strict OR the strategy is unreliable."

---

### **Row 7: User Decision Framework (The "Action" Row)**

**Panel 7.1: Backtest Validation Checklist (Table with Checkmarks)**

- **What it shows:**
  - [✓] Sharpe Ratio > 1.5
  - [✓] Max Drawdown < 15%
  - [✓] Win Rate > 40%
  - [✓] Profit Factor > 1.3
  - [✓] Positive returns in 60%+ of months
  - [✓] Works in both bull and bear markets
  - [✓] Fees + Slippage < 30% of gross PnL
- **User Education:**
  - "All boxes must be checked to proceed to Shadow mode."
  - "Each failed check is a red flag. 3+ failures = reject strategy."

**Panel 7.2: Recommended Next Steps (Text Panel with Dynamic Logic)**

- **What it shows:** Context-aware guidance based on metrics
  - *If all metrics pass:* "✅ Strategy validated. Proceed to Shadow Dashboard for paper trading."
  - *If drawdown too high:* "️ Reduce position size by 50% and re-backtest."
  - *If win rate too low:* "⚠️ Tighten entry criteria or improve Alpha Bridge signal filtering."
  - *If decay detected:* "⚠️ Strategy works only in specific regime. Add regime filter."
- **User Education:**
  - "This is your personalized action plan. Follow it before deploying capital."

---

## 📚 USER EDUCATION: HOW TO INTERPRET BACKTEST RESULTS

### **The 5-Minute Validation Protocol**

When reviewing a backtest, users should follow this exact sequence:

**Minute 1: The Sanity Check**

- Look at Row 1 (Executive Summary)
- Ask: "Are all scorecard metrics green?"
- If NO → Stop. Reject strategy. Do not proceed.
- If YES → Continue.

**Minute 2: The Pain Test**

- Look at Row 2 (Drawdown Analysis)
- Ask: "Could I have survived the worst drawdown emotionally and financially?"
- If NO → Reduce position size 50% and re-backtest.
- If YES → Continue.

**Minute 3: The Reality Check**

- Look at Row 3 (Trade Quality)
- Ask: "After fees and slippage, is there still meaningful profit?"
- If NO → Strategy is too high-frequency or low-edge. Reject.
- If YES → Continue.

**Minute 4: The Context Check**

- Look at Row 4 (Market Regime)
- Ask: "Does this work in multiple market conditions, or just one?"
- If ONE CONDITION ONLY → Add regime filter or reject.
- If MULTIPLE CONDITIONS → Continue.

**Minute 5: The Decision**

- Look at Row 7 (Validation Checklist)
- Count checkmarks:
  - 7/7 → ✅ **APPROVED** → Move to Shadow Dashboard
  - 5-6/7 → ️ **CONDITIONAL** → Fix issues, re-backtest
  - <5/7 → ❌ **REJECTED** → Return to strategy development

---

## 🎨 DASHBOARD LAYOUT SPECIFICATIONS

### **Visual Design Principles**

1. **Color Coding:**
   - Green: Positive/good metrics
   - Red: Negative/danger metrics  
   - Yellow: Warning/caution metrics
   - Gray: Neutral/informational

2. **Time Range Selector:**
   - Default: "Entire Backtest Period"
   - Quick filters: "Last 30d", "Last 90d", "Bull Market Only", "Bear Market Only"
   - Custom range picker

3. **Refresh Rate:**
   - Backtest dashboards do NOT auto-refresh
   - Manual "Regenerate" button only (to prevent accidental re-runs)

4. **Export Functionality:**
   - "Download PDF Report" button (generates full backtest report)
   - "Share Snapshot" button (creates read-only link for team review)

---

## DASHBOARD NAVIGATION FLOW

```
Backtest Dashboard
    ↓ (if validated)
Shadow Dashboard (Paper Trading)
    ↓ (if profitable after 30d)
Live Dashboard (Real Capital)
    ↓ (if drawdown exceeded)
← ← ← Return to Backtest for recalibration
```

---

## ✅ BACKTEST DASHBOARD SUCCESS CRITERIA

A user should be able to answer these questions within 5 minutes of viewing the dashboard:

1. **Profitability:** "Did this strategy make money, and was it enough to justify the risk?"
2. **Survivability:** "Could I have emotionally and financially survived the worst periods?"
3. **Robustness:** "Does this work in different market conditions, or am I overfit?"
4. **Reality:** "After real-world costs (fees, slippage), is there still an edge?"
5. **Action:** "Do I approve this for paper trading, or send it back for revision?"
