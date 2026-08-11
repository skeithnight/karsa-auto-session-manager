# Risk Management & Operations Runbook
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Classification:** CRITICAL / SAFETY  
**Last Revised:** 2026-08-11

---

## 1. Emergency Kill Switch (Manual Intervention)

**Purpose:** Instantly halt trading, flatten open positions, and exit in `< 10 seconds` without waiting for normal APM or strategy logic.

### Triggers
1. **Telegram Command (Primary)**: Authorized user issues `/kill` or `/stop` via the `karsa-commander` bot interface.
2. **Local File Flag (Backup)**: Creation of flag file `touch /tmp/KILL_KARSA` on the container host.

### Emergency Sequence
1. **Cancel All**: Send a batch request to Bybit to cancel all open orders.
2. **Market Flatten**: Issue immediate market close orders for all active positions.
3. **Halt Loop**: Set the global `kill_switch` `asyncio.Event`.
4. **Alert**: Push Telegram alert: *"🚨 KILL SWITCH ACTIVATED. All orders canceled, positions flattened."*

---

## 2. Automated Circuit Breakers

| Breaker Name | Trigger Condition | Automated Action |
| :--- | :--- | :--- |
| **Portfolio Daily Loss** | Cumulative daily account equity loss exceeds **2.0%** from midnight UTC baseline. | **DAILY HALT:** `PortfolioRiskManager` blocks all new entries for the rest of the UTC day. Sets `risk:portfolio_cb:daily_loss_fired = 1`. |
| **Consecutive Losses** | **3** consecutive losing trades recorded. | **4-HOUR COOLDOWN:** Pauses new signal generation for 4 hours. Triggers diagnostic analysis. |
| **Execution Latency Spike** | Average Bybit order execution latency exceeds **1500ms** over 5 minutes. | **PAUSE ENTRIES:** Blocks new order placement; alerts operator via Telegram. |
| **Data Engine Feed Stale** | No WebSocket tick updates received for **> 15 seconds**. | **HALT SIGNALS:** Pauses signal generation until feed recovers. Existing positions protected by exchange SL. |
| **Mandatory AI Failure** | 9router proxy returns error or timeout on pre-entry evaluation. | **REJECT SIGNAL:** Forces confidence to `0.0`, guaranteeing deterministic signal rejection. |
| **ISP DNS Poisoning** | Default UDP 53 DNS queries to `api.bybit.com` return Telkomsel block IPs (`182.23.*`). | **DOH BYPASS:** Automatically queries Cloudflare DoH (`https://1.1.1.1/dns-query`) to resolve AWS CloudFront IPs in ~50ms. |

---

## 3. Active Position Manager (APM) Operational Guardrails

Every open position is continuously monitored by the `ActivePositionManager` (`app/execution/position_manager.py`):
1. **Mandatory Exchange-Side Stop Loss**: Hard Stop Loss order MUST be placed on Bybit server immediately upon order fill.
2. **Hard 5% SL Cap**: Stop Loss level is strictly capped at a maximum 5% distance.
3. **Breakeven Lock at +1R**: Once unrealized profit reaches +1R, Stop Loss is automatically amended to entry price (breakeven).
4. **Orphan Minimum Clean-up**: Residual position size below 5 USDT notional is automatically closed.
5. **Regime Shift Kill Switch**: If `RegimeClassifier` detects an unfavorable regime shift (e.g. `TREND_BULL` to `TREND_BEAR` on a Long position), APM market-closes the position immediately.

---

## 4. Disaster Recovery & Startup Reconciliation

Upon boot or restart, Karsa ASM executes the **"Trust Nothing" Startup Reconciliation Protocol**:
1. Query Bybit REST API for all active open positions and open orders.
2. Query local PostgreSQL `trades` and Redis `karsa:positions:*` for internal state.
3. **Reconcile**:
   - Cancel orphaned Bybit orders unknown to internal state.
   - Sync local database to match Bybit exchange truth.
   - Register exchange-side Stop Loss orders with `PositionStore`.
