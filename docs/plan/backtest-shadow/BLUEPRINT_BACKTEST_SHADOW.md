This is the **Master Blueprint** for the ultimate, institutional-grade, self-driving version of **Karsa ASM**. 

This plan integrates every architectural decision we’ve discussed: the 5-container microservices, the Adaptive Multi-Strategy logic, the Shadow/Backtest parallel execution, the Telegram Commander, and your specific requirement to **choose the risk percentage dynamically upon launch**.

Save this document. This is your exact roadmap from today until the bot is fully autonomous.

---

# 🏛️ THE KARSA ASM MASTER BLUEPRINT

## 🏗️ Phase 1: Infrastructure & The 5-Container Fleet
*Goal: Build a scalable, crash-proof, and data-consistent foundation.*

You will deploy a unified `docker-compose.yml` containing 5 distinct services.

1. **`karsa-data-engine`**: The sole bridge to the outside world. Connects to Bybit/Binance/OKX WebSockets *once*. Normalizes data, calculates base indicators, and broadcasts via **Redis Pub/Sub**. Caches historical data to PostgreSQL for backtesting.
2. **`karsa-live`**: Subscribes to Redis. Executes real trades. Routes execution through `gluetun` (VPN).
3. **`karsa-shadow`**: Subscribes to Redis. Executes virtual trades. Uses the exact same codebase as Live but with `SHADOW_MODE_ENABLED=true`.
4. **`karsa-backtest`**: A worker container. Reads historical data from PostgreSQL. Runs batch simulations on demand.
5. **`karsa-commander`**: Hosts the Telegram Bot API. Manages the UI, compares metrics, and pushes hot-reloads to the other containers.

**Infrastructure Additions:**
*   **PostgreSQL 16**: Stores trade state, historical candles, and shadow trade logs.
*   **Redis 7**: The central nervous system. Handles Pub/Sub (data), state caching, and config hot-reloading.
*   **Grafana + Loki + Prometheus**: For the Signal Funnel dashboards and centralized JSON log pooling.

---

## 🧠 Phase 2: Core Trading Engine Upgrades (The "Brain")
*Goal: Implement the Adaptive Multi-Strategy logic to trade all market conditions profitably.*

**1. Per-Coin Regime Classification**
*   Stop using BTC for everything. The `RegimeClassifier` calculates ADX, Hurst, and ATR using the *target symbol's* local candles.
*   Outputs: `TREND_BULL`, `TREND_BEAR`, `RANGE`, `CHOP`.

**2. Granular Confluence Scoring (Strategy Router)**
*   **TREND**: Scores on momentum, volume surge, and Global Fakeout Detection (Binance/OKX sync).
*   **RANGE**: Scores on Bollinger Band extremes, wick rejections, and RSI divergence.
*   **CHOP**: Scores on 4 micro-structure components (+20 Orderbook Absorption, +20 Wick Snap-back, +30 Funding Confluence, +30 OI Drop). *Requires 70+ to pass.*

**3. Dynamic Risk & Execution Profiles**
*   **Spread Gate**: Dynamic per regime (TREND: 0.08%, RANGE: 0.15%, CHOP: 0.30%).
*   **Execution Mandate**: If spread > 0.15% OR regime is CHOP/RANGE, force `Post-Only` Limit orders to capture Maker fees/rebates.

**4. Active Position Manager (APM)**
*   Runs every 2 seconds.
*   Implements **+1R Scale-Out & Breakeven** (locks in profit, eliminates losers).
*   Implements **Worst-Price-Seen** logic to prevent "wick misses" on Stop Losses.
*   Implements **Time-Based Kills** (e.g., CHOP max hold 30 mins).

---

## 👻 Phase 3: Shadow Mode & Backtesting Infrastructure
*Goal: Prove the math and the code without risking capital.*

**1. Shadow Mode Implementation (`app/execution/shadow.py`)**
*   **`ShadowExecutor`**: Intercepts SOR calls. Applies asymmetric fees (Maker vs Taker based on order type) and 0.05% simulated slippage.
*   **`ShadowAPM`**: Monitors live Redis prices. Manages virtual SL/TP. Deducts 8-hour funding rate drag.
*   **State Isolation**: Writes to `shadow_trades` table and `shadow:position:*` Redis keys to prevent collision with Live.

**2. Backtest Worker**
*   Listens to a Redis queue (`backtest_jobs`).
*   When triggered, pulls historical candles from Postgres, runs the exact same `StrategyRouter` and `APM` logic, and outputs a JSON report to the `backtest_results` table.

---

## 📱 Phase 4: The Commander & Telegram Control Plane
*Goal: Build the self-driving UI for launching, monitoring, and auto-adjusting the bot.*

**1. The "Launch ASM" Flow (Dynamic Risk Sizing)**
When you send `/start` or `/launch` to the Telegram bot, the Commander initiates the launch sequence:
> **🚀 Launch Karsa ASM**
> *Select initial capital risk allocation for the Live container:*
> 
> **[ 🟢 10% (Canary/Safe) ]**
> **[ 🟡 25% (Moderate) ]**
> **[ 🔴 50% (Aggressive) ]**
> **[ ⚫ 100% (Full Send) ]**

*   **Action:** Once you click a button, the Commander writes `{"global_risk_multiplier": 0.10}` to the Redis key `karsa:config:live_overrides`.
*   **Hot-Reload:** The `karsa-live` container's `config_watcher_task` instantly reads this and sets the global position size multiplier. The bot starts trading immediately at your chosen risk level.

**2. The Main Dashboard & Inline Keyboards**
> **🤖 Karsa Command Center**
> **Live (10% Risk):** 🟢 +$12.40 | **Shadow:** 🟢 +$45.20
> 
> **[ 📊 Live Details ]**  **[ 👻 Shadow Report ]**
> **[ 🧪 Run Backtest ]**   **[ ⚙️ Auto-Adjustments ]**

**3. The Auto-Adjustment Engine (The "Self-Driving" Logic)**
The Commander runs a cron job every 6 hours comparing Shadow vs. Backtest:
*   *Trigger:* If Shadow 7-Day CHOP Win Rate (45%) < Backtest 30-Day CHOP Win Rate (65%).
*   *Diagnosis:* "Market is choppier than historical norms. CHOP gate too loose."
*   *Recommendation UI:*
    > **⚠️ ADJUSTMENT RECOMMENDED**
    > Increase CHOP confidence gate from 65 → 75.
    > Reduce CHOP size multiplier from 0.3x → 0.15x.
    > 
    > **[ ✅ Apply to Live ]**  **[ ❌ Reject ]**
*   *Execution:* If you click Apply, the Commander pushes the new thresholds to Redis. `karsa-live` hot-reloads them instantly without restarting.

---

## 📊 Phase 5: Telemetry & Observability
*Goal: See exactly where signals die and how money is made.*

**1. The Signal Funnel (Prometheus/Grafana)**
Track the exact drop-off rate: `Received -> Regime Classified -> Strategy Scored -> Confidence Passed -> Risk Passed -> Executed`.

**2. Centralized Logging (Loki)**
*   Python `loguru` configured to output strict JSON.
*   **Log Aggregation:** 5-minute summaries for pipeline kills (prevents log spam).
*   **Levels:** `DEBUG` (raw ticks), `INFO` (regime changes, executions, shadow PnL), `WARNING` (spread widening, ghost exits), `ERROR` (API drops).

**3. Regime-Specific Telemetry**
Track Win Rate and Profit Factor *per regime* (`karsa_win_rate{regime="CHOP"}`). This tells you exactly which strategy is decaying.

---

## 🚀 Phase 6: The Go-Live Protocol (Execution Timeline)

Do not build this all at once. Follow this strict 4-week deployment schedule.

### Week 1: The Core & The Data Engine
*   Build the `karsa-data-engine` and migrate Live/Shadow to use Redis Pub/Sub.
*   Implement Per-Coin Regime and Granular CHOP scoring.
*   *Milestone:* The bot processes live data without IP bans, and the CHOP dead-zone is eliminated in local tests.

### Week 2: Shadow Mode & The Commander
*   Build `ShadowExecutor`, `ShadowAPM`, and the `shadow_trades` DB schema.
*   Build the `karsa-commander` Telegram bot with the **Launch Risk Selection** and basic dashboards.
*   *Milestone:* You can launch the bot via Telegram, select "10% Risk", and watch Shadow Mode generate virtual trades in the background.

### Week 3: Backtest Integration & Auto-Adjustments
*   Build the `karsa-backtest` worker and the historical data cache.
*   Implement the Auto-Adjustment logic (Shadow vs Backtest delta) and the Redis Hot-Reload mechanism in `karsa-live`.
*   *Milestone:* You can trigger a backtest from Telegram, see the results, and successfully push a parameter tweak to the Live bot without restarting it.

### Week 4: The 14-Day Burn-In (Go-Live)
*   **Days 1-7:** Launch via Telegram at **10% Risk**. Monitor the "Live vs Shadow Delta". Ensure slippage and fill rates match expectations.
*   **Days 8-14:** If stable, use the Telegram UI to hot-reload the risk to **25%**, then **50%**.
*   *Milestone:* The system is fully autonomous, self-monitoring, and generating real yield.

---

### 💡 Final Architectural Truth

You are no longer just building a trading script. You are building a **Distributed Algorithmic Trading Platform**. 

By separating the Data Engine, isolating the Shadow/Backtest environments, and controlling it all via a Telegram UI with hot-reload capabilities, you have eliminated the three biggest killers of retail algo traders:
1.  **Overtrading/Bad Logic** (Solved by Granular Scoring & Auto-Adjustments).
2.  **Infrastructure Blindsides** (Solved by Shadow Mode & Data Parity).
3.  **Emotional Tampering** (Solved by the Commander UI and strict risk-launch protocols).

Open your IDE. Create the `docker-compose.yml` for the 5 containers. The journey to the institutional tier starts today.