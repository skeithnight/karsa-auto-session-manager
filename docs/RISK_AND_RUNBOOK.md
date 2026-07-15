# Risk Management & Operations Runbook
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Locked  
**Classification:** CRITICAL / SAFETY  

---

## 1. Emergency Kill Switch (Manual Intervention)
**Purpose:** Allow the operator to instantly halt all trading and flatten all positions in `< 10 seconds`, bypassing all normal logic, risk gates, and smart order routing.

### Implementation Mechanisms
The system will listen for two distinct "Kill" triggers concurrently in the main `asyncio` loop:

1. **Telegram Command (Primary):** 
   * The bot listens for a specific Telegram message (e.g., `/kill_karsa`) from the authorized `TELEGRAM_CHAT_ID`.
2. **Local File Flag (Backup):** 
   * The Watchdog checks for the existence of a specific file on the host machine every 1 second (e.g., `touch /tmp/KILL_KARSA`).

### The Kill Sequence (Must execute in < 10s)
When triggered, the bot immediately executes the following sequence, ignoring all errors or timeouts:
1. **Cancel All:** Send a batch request to Bybit to cancel **all** open limit orders.
2. **Market Flatten:** Send Market Close (IOC) orders for **all** open positions.
3. **Halt Loop:** Set the global `asyncio.Event` to stop the Alpha Bridge and Data Engine.
4. **Final Alert:** Send a final Telegram message: *"🚨 KILL SWITCH ACTIVATED. All positions flattened. Bot halted."*
5. **Exit:** Terminate the Python process (`sys.exit(1)`).

---

## 2. Automated Circuit Breakers (System Intervention)
**Purpose:** Pre-defined, deterministic rules that force the bot to halt itself before a human needs to intervene.

| Breaker Name | Trigger Condition | Bot Action |
| :--- | :--- | :--- |
| **Daily Drawdown (Hard)** | Realized + Unrealized PnL drops **> 2%** from the starting daily equity. | **HARD STOP:** Cancel all orders, flatten all positions, halt bot, alert Telegram. |
| **Consecutive Losses (Soft)** | **3** losing trades in a row. | **SOFT STOP:** Halt *new* trade generation for 60 minutes. Existing positions are managed normally. |
| **Execution Latency Spike** | Average order execution latency exceeds **1500ms** over a 5-minute rolling window. | **HALT:** Cancel open orders, pause new entries. Alert Telegram: *"Proxy degradation detected."* |
| **Margin Utilization** | Total Bybit margin used exceeds **40%** of total account equity. | **HALT:** Block all new position openings. Allow existing positions to be managed/closed. |
| **Stale Data** | Global Read Engine (Binance/OKX) receives no WebSocket updates for **> 15 seconds**. | **HALT:** Pause Alpha generation. Do not open new trades. Alert Telegram. |
| **AI Call Latency** | AI analyst p95 latency exceeds **5 seconds** over a 5-minute rolling window. | **DEGRADE:** Skip AI for next cycle, use deterministic confidence only. Alert Telegram: *"AI degraded, running deterministic."* |
| **AI Confidence Anomaly** | AI returns confidence **< 0.20** for **10+ consecutive** signals. | **ALERT:** Possible model degradation or market regime shift. Telegram: *"AI confidence anomaly — review recommended."* |
| **AI Unavailable** | 9router returns error or timeout on **3 consecutive** calls. | **HALT SIGNALS:** AI is mandatory — no bypass. All signals rejected until AI recovers. Alert Telegram: *"AI offline, signals halted."* |
| **Universe Scorer Empty** | Universe scorer returns **0 symbols** above threshold. | **FALLBACK:** Use static symbol list from `config.py`. Alert Telegram: *"Universe empty, fallback to static list."* |
| **ASM Session Inactive** | `karsa:auto:state:active` is `"0"` or missing in Redis. | **BLOCK:** Executor skips all signals. No new positions opened. Data pipeline stays warm. Existing positions managed normally. |

---

## 3. Proxy Failover (WARP Degradation Protocol)
**Context:** Because Bybit is geo-blocked, the WARP SOCKS5 proxy is a mandatory single point of failure. If the proxy drops, we cannot trust our execution latency.

### Detection
The Watchdog monitors the Bybit Private WebSocket heartbeat and REST API response times. 

### Failover Actions
If the WARP proxy drops or latency spikes > 2000ms:
1. **DO NOT attempt to Market Flatten immediately.** Sending market orders through a degraded proxy will result in catastrophic slippage or failed requests, leaving the bot blind.
2. **Cancel Pending Orders:** Immediately cancel all open Limit orders. *(Reason: We don't want a limit order to accidentally fill 5 minutes later when the proxy reconnects while we are unaware).*
3. **Rely on Exchange-Side Stops:** Ensure all open positions have **hard Stop-Loss orders resting on the Bybit exchange server** (not just in the bot's memory). 
4. **Halt Trading:** Stop the Alpha Bridge from generating new signals.
5. **Alert Human:** Send Telegram alert: *"⚠️ WARP Proxy Degraded. Open orders canceled. Existing positions protected by exchange-side SL. Bot paused."*
6. **Resume:** The bot will automatically attempt to reconnect the proxy every 30 seconds. Once stable for 60 seconds, it will resume trading.

---

## 4. Disaster Recovery & State Reconciliation
**Purpose:** Recover from catastrophic failures (Docker crash, Postgres volume corruption, unexpected server reboot) without creating "ghost" positions or desynced state.

### The "Trust Nothing" Startup Protocol
When the bot starts (or restarts), it **must not** trust the local PostgreSQL database. It must execute the following Reconciliation Sequence:

1. **Fetch Exchange Truth:** Query Bybit REST API for all actual open positions and all active open orders.
2. **Fetch Local Truth:** Query the local Postgres DB for the last known state.
3. **Compare & Resolve:**
   * *Scenario A (Clean):* Bybit and Postgres match perfectly. Proceed to normal startup.
   * *Scenario B (Orphaned Orders):* Bybit has open limit orders that Postgres doesn't know about. **Action:** Cancel them immediately via Bybit API.
   * *Scenario C (Ghost Positions):* Postgres says we are LONG 1 BTC, but Bybit says we are FLAT. **Action:** Overwrite Postgres with Bybit's truth. Log a `CRITICAL` error to Prometheus/Telegram.
   * *Scenario D (Postgres Dead):* Postgres connection fails. **Action:** Create a fresh, empty Postgres schema, populate it with Bybit's current state, and proceed.
4. **Sync Complete:** Only after this sequence finishes successfully does the Watchdog give the "Green Light" to the Alpha Bridge to start generating signals.

---

## 5. Exchange Outage Handling

### Read Exchanges (Binance, OKX) Go Down
* **Impact:** We lose the "Global State" (VWAP, Skew). Our alpha is blind.
* **Action:** The Data Engine flags the specific exchange as `STALE`. The Alpha Bridge automatically excludes that exchange from its calculations. If > 50% of read exchanges are stale, the bot halts new entries.

### Write Exchange (Bybit) Goes Down
* **Impact:** We cannot execute, amend, or cancel orders.
* **Action:** 
  1. Halt all new signal generation.
  2. Rely entirely on **Exchange-Side Stop Losses** to protect open capital.
  3. Continuously attempt to reconnect the Bybit WebSocket.
  4. Alert Telegram: *"Bybit Outage. Trading paused. Positions protected by server-side SL."*

---

## 6. Operations Runbook Matrix

A quick-reference guide for the operator when alerts fire.

| Alert / Symptom | Bot's Automated Action | Human Operator Action |
| :--- | :--- | :--- |
| **🚨 KILL SWITCH ACTIVATED** | Flattened all, bot stopped. | Investigate why it was triggered. Check Bybit UI to confirm flat. Restart bot manually when ready. |
| **🛑 Daily Drawdown > 2%** | Flattened all, bot stopped. | Review trade logs in Postgres. Analyze if market regime changed. Reset daily equity tracker tomorrow. |
| **⚠️ VPN Tunnel Down** | Paused trading, stale data warnings. | Check `docker logs karsa-gluetun`. Verify WireGuard server is running on droplet. Check DO Cloud Firewall allows UDP 51820. |
| **📉 Stale Data (>15s)** | Paused new entries. | Check VPN tunnel. Check if Binance/OKX are experiencing global outages. |
| **⏳ Execution Latency > 1500ms** | Paused new entries. | Check Docker resource usage. Check VPN routing. |
| **💀 Postgres Connection Failed** | Rebuilt state from Bybit, continued. | Check Docker logs for Postgres container. Restart Postgres container (`docker compose restart db`). |
| **⚠️ Reconciliation Degraded** | Startup continues in degraded mode (data engine + alpha bridge run). | Check Bybit API key permissions ("Asset" read required). Verify VPN tunnel is up. Positions cannot be verified until Bybit reachable. |
| **🔄 State Divergence Detected** | Canceled orphaned orders, synced DB. | Review `CRITICAL` logs. This indicates a bug in the execution logic or a missed WebSocket message. |
| **🤖 AI Analyst Timeout** | Signals rejected (AI mandatory). | Check 9router health (`curl http://127.0.0.1:20129/health`). Check Anthropic API status. If persistent, temporarily set `ai_analyst_enabled=false` in `.env` (explicit flag, logged). |
| **🤖 AI Position Judge All HOLDs** | Positions never exiting via AI. | Check consecutive hold counter in position store. Verify 3-HOLD forced exit is working. Review AI reasoning in Telegram alerts. |
| **📊 Universe Scorer Empty** | Fell back to static symbol list. | Check if market-wide volume drop or data issue. Manually trigger `/universe` refresh via Telegram. |
| **🧠 Trade Memory Corrupted** | AI prompts with garbage context. | Flush `karsa:memory:*` Redis keys. Rebuild from PostgreSQL `trades` table if needed. |
| **⚙️ Sector Cap Rejected** | Signal blocked by sector diversity. | Review current sector allocation via `/status`. Adjust `sector_cap_max` in config if needed. |

---

## 7. Mandatory Safety Implementations (Code Level)

To enforce this runbook, the following code patterns are **mandatory** for the development team:

1. **Exchange-Side Stop Losses:** Every time the Bybit Executor opens a position, it **must** immediately place a hard Stop-Loss order on the Bybit exchange server. The bot's internal "soft" stop-loss is secondary. If the bot dies, the exchange SL saves the capital.
2. **Idempotent Execution:** The execution logic must be idempotent. If the bot crashes while sending an order, and restarts, the State Reconciliation engine must ensure it doesn't accidentally send the exact same order twice.
3. **Graceful Shutdown:** The `main.py` must catch `SIGINT` (Ctrl+C) and `SIGTERM` (Docker stop). Upon catching these, it must execute the **Kill Switch Sequence** (Cancel orders -> Flatten -> Exit) before allowing the process to die. *Never just kill the process without flattening.*
