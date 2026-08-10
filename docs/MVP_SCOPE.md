# Minimum Viable Product (MVP) Scope
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Locked
**Last Revised:** 2026-07-17 — WARP→WireGuard cleanup
**Target Environment:** Local Docker (Paper Trading / Bybit Testnet)  

---

## 1. The MVP Objective
The goal of the MVP is to **prove the core thesis ("Read Global, Execute Local") safely and reliably in a paper-trading environment**. 

We are not trying to build the perfect, AI-driven, multi-exchange hedge fund on day one. We are building a robust, fault-tolerant pipeline that ingests global data, calculates a basic structural edge, and executes it on Bybit through a proxy without losing money to software bugs or state divergence.

## 2. The Golden Rule of the MVP
> **"If it is not explicitly listed in the IN SCOPE section, we do not code it, we do not design it, and we do not discuss it until V1 is consistently profitable."**

This rule protects the project from "shiny object syndrome" (e.g., adding Reinforcement Learning or LLM agents before the basic WebSocket connections are stable).

---

## 3. IN SCOPE (What We Build First)

### A. Architecture & Infrastructure
*   **Single-Process Monolith:** The Orchestrator (data/alpha) and the Bot (execution) are merged into a single Python `asyncio` application. This eliminates internal network hops and state-sync issues.
*   **Local Docker Stack:** `docker-compose.yml` provisioning the App, PostgreSQL (for persistent logging), and basic Prometheus (for metrics).
*   **State Reconciliation:** A mandatory startup script that queries Bybit for actual open positions and syncs them to the local Postgres database before the trading loop begins.
*   **Redis Cache Layer:** Redis is used for high-speed state caching (`global:state:{symbol}`, `system:heartbeat`, `system:circuit_breaker`, `system:config:regime`) and session state. Already implemented in codebase.

### B. Data Ingestion (The "Read" Pipeline)
*   **CCXT Pro WebSockets:** Persistent, auto-reconnecting WebSocket connections to **Binance** (and optionally OKX/Bybit public feeds) for the dynamic universe of ~60 liquid pairs (filtered via `crypto_universe.py` for volume, momentum, squeeze, and overextension, excluding TradFi perps and fresh listings).
*   **Data Normalization:** Standardizing incoming ticker and L2 order book data into a unified internal Python dictionary (in-memory state).
*   **Bad Tick Filtering:** A basic statistical filter to reject obvious exchange API glitches (e.g., price spikes > 5% in a single tick).

### C. Alpha & Signal Generation (The "Brain")
*   **Basic Global Metrics:** Calculation of **Global VWAP** and **Global Order Book Skew** (Bid vs. Ask volume ratio across the read exchanges).
*   **Basic Lead-Lag Math:** Simple threshold logic comparing Binance price movement against Bybit price movement on a 15-minute rolling window.
*   **Multi-Signal Composite:** Weighted confidence: `regime_mult × (0.4×skew + 0.3×lead_lag + 0.2×funding + 0.1×oi)`.
*   **Regime Detection:** Hurst + ADX + EMA200 on BTC 1H. CHOP halts all trading.
*   **Multi-Timeframe Confirmation:** 4H EMA trend check, penalizes signals contradicting higher timeframe.
*   **Dynamic Universe Scoring:** Replace static symbol list with Volume+Momentum+Squeeze+Overextension scoring.
*   **AI CryptoAnalyst (MANDATORY):** LLM-powered pre-entry analysis via 9router proxy. Final confidence = quant × 0.5 + AI × 0.5. If AI fails, it returns 0 confidence, guaranteeing REJECT.
*   **Signal Output:** Generation of a directional signal (`LONG`, `SHORT`, `FLAT`) with confidence score.

### D. Execution (The "Write" Pipeline)
*   **Bybit-Only Execution:** The bot will strictly place, amend, and cancel orders on Bybit.
*   **WireGuard VPN Integration:** All Bybit traffic (REST and Private WebSockets) and 9router AI proxy traffic must be routed through the self-hosted WireGuard VPN tunnel via `gluetun` sidecar.
*   **Private WebSockets:** Use Bybit's Private WebSocket channel for order management to minimize proxy handshake latency.
*   **Basic Smart Order Routing (SOR):** A simple 3-step execution logic: 
    1. Try Post-Only Limit Order. 
    2. If unfilled after X seconds, reprice. 
    3. Fall back to Market/IOC order to ensure fill.

### E. Risk & Telemetry
*   **Simplified 3-Layer Risk Gate:** (Stripped down from 9 layers for MVP).
    1. *Global Liquidity:* Is the aggregated 24h volume above the minimum threshold?
    2. *Proxy/Spread Health:* Is the price spread between Binance and Bybit abnormally wide? (If yes, halt).
    3. *Hard Circuit Breaker:* If daily unrealized + realized PnL drops below -2% (per `RISK_AND_RUNBOOK.md`), 4-hour cooldown: cancel open orders, block new entries, and trigger System Doctor AI.
*   **Postgres Logging:** Every signal generated, risk check passed/failed, and order placed must be written to the `trades` and `logs` tables.
*   **Basic Prometheus Metrics:** Expose `/metrics` for: `orders_placed_total`, `order_latency_seconds`, and `websocket_disconnects_total`.

---

## 4. OUT OF SCOPE (What We Explicitly Reject for V1)

If you find yourself wanting to build these, put them in the `docs/IDEAS_BACKLOG.md` file and close the IDE.

*   ❌ **LLM in the Hot Execution Path (SOR/Risk Gate):** No LLMs in the SOR or risk gate — latency and determinism requirements forbid it. **AI is MANDATORY in two safe positions:** pre-entry CryptoAnalyst and post-entry PositionJudge, via 9router proxy. See `docs/review/ai_layer_analysis.md`.
*   ❌ **Multi-Exchange Execution:** We are not building cross-exchange arbitrage or routing orders to OKX/Binance. We only *read* from them. We only *write* to Bybit.
*   ❌ **Complex RL / FinRL Execution:** No Reinforcement Learning agents for order slicing. We use deterministic, rule-based SOR.
*   ❌ **Microservice Split:** We are not separating the Orchestrator and Bot into different Docker containers communicating via Redis Pub/Sub. It introduces fatal state-divergence risks.
*   ❌ **Advanced Portfolio Correlation Math:** For the MVP, we treat every trade independently. We are not calculating rolling correlation matrices across open positions.
*   ❌ **Grafana Dashboards:** While Prometheus is in scope for raw metrics, building beautiful Grafana UI dashboards is a distraction. We will read raw metrics or logs for V1.
*   ❌ **Trading Low-Cap Altcoins:** Strictly Top liquid perps for the MVP.

---

## 5. MVP Phased Delivery Plan

We will build the MVP in four strict, sequential phases. We do not move to the next phase until the current one passes the Definition of Done.

### Phase 1: The Nervous System (Data & Infra)
*   Setup Docker Compose (App, Postgres, Prometheus).
*   Implement CCXT Pro WebSocket manager for Binance.
*   Implement data normalization and bad-tick filtering.
*   *Deliverable:* A script that runs continuously, prints normalized global VWAP/Skew to the console, and survives network drops.

### Phase 2: The Hands (Execution & Proxy)
*   Implement Bybit Private WebSocket connection via WireGuard VPN tunnel (`gluetun`).
*   Implement the Basic SOR (Limit -> Reprice -> Market).
*   Implement State Reconciliation on startup.
*   *Deliverable:* A script that can successfully place, track, and close a dummy market order on Bybit Testnet through the WireGuard VPN tunnel, logging the exact latency.

### Phase 3: The Brain & The Shield (Alpha & Risk)
*   Implement the Basic Lead-Lag and Global Skew math.
*   Implement the Simplified 3-Layer Risk Gate.
*   Implement the Hard Circuit Breaker (-2% daily loss).
*   *Deliverable:* The system generates signals based on global data, filters them through the risk gate, and logs the decisions to Postgres.

### Phase 4: Integration & Paper Trading
*   Merge Phase 1, 2, and 3 into the single `main.py` event loop.
*   Connect Prometheus metrics.
*   *Deliverable:* The bot runs autonomously on Bybit Testnet for 72 hours with zero crashes, zero state divergences, and correctly logs all trades to Postgres.

---

## 6. MVP Success Criteria (Graduating to V1.1)
The MVP is considered **successful** and ready for live capital (V1.1) only when:
1.  **Stability:** It has run for 14 consecutive days on Bybit Testnet without a single unhandled exception or state divergence.
2.  **Execution:** Average execution latency (Signal -> Bybit Fill) is consistently under 800ms via the WireGuard VPN tunnel.
3.  **Safety:** The Hard Circuit Breaker was intentionally triggered during testing and successfully flattened the account and halted the bot.
4.  **Profitability (Paper):** The basic Global Skew/Lead-Lag math yields a positive expectancy (win rate > 50% with a reward/risk ratio > 1.2) over the 14-day testnet period.

---

## 7. Win-Rate Enhancement (Post-MVP-Bootstrap)

> Added after codebase audit. See `docs/review/execution_plan.md` for full plan.

Once the MVP core pipeline (Phase 1–4 of §5) is stable, the following enhancements target >80% win rate on testnet:

| Phase | Scope | Effort | Status |
| :--- | :--- | :--- | :--- |
| **0A** | Wire real bid/ask/volume into RiskGate | ~30 min | ✅ DONE |
| **0B** | Exchange-side Stop Loss on fill (BybitClient + SOR) | ~2-3h | ✅ DONE |
| **1** | Regime Engine (Hurst + ADX on BTC 1H) | ~4-5h | ✅ DONE |
| **2** | Multi-signal confidence (lead-lag + funding + OI) | ~5-6h | ✅ DONE |
| **3** | Entry quality filter (spread, book depth, time-of-day) | ~2-3h | ✅ DONE |
| **4** | Position lifecycle (trailing stop + performance checkpoints) | ~6-8h | ✅ DONE |
| **5** | Wire executor_task → `sor.execute()` (unblock chain) | ~30 min | ✅ DONE |
| **6** | Dynamic universe scoring | ~4-5h | ✅ DONE |
| **7** | Multi-timeframe confirmation (4H) | ~2-3h | ✅ DONE |
| **8** | AI CryptoAnalyst mandatory (remove toggle) | ~1-2h | ✅ DONE |
| **9** | Trade memory injection | ~2-3h | ✅ DONE |
| **10** | Sector diversity cap | ~2-3h | ✅ DONE |

**Status:** All Phases 0A–10 are fully completed and wired end-to-end.

**Deferred (post V1.1):** Grafana dashboards (out of scope), Reinforcement Learning.

---

## 8. DeFi Scope Expansion (v3.0 — Ratified per ADR-010)

Per **ADR-010**, KASM expands from CEX-only (v2.1) into a Hybrid Intelligence Trading System (v3.0). This section supersedes the strict "Bybit-Only Execution" and "No Multi-Exchange Execution" out-of-scope constraints for Phase 8+ as follows:

| Component | Scope | Gating Criteria | Status |
| :--- | :--- | :--- | :--- |
| **On-Chain Data Ingestion** | Read-only EVM pool feeds (Uniswap v3/v4) + Gas Tracker | Phase 1 shadow stability pass | 🟢 IN SCOPE (Phase 3) |
| **LVR Calculator** | CEX-DEX price discrepancy & gas-adjusted EV scoring | Phase 3 completion | 🟢 IN SCOPE (Phase 4) |
| **Treasury Automation** | Idle USDC routing to Pendle PT / HLP Vault yield | Phase 4 completion + 30-day shadow proof | 🟢 IN SCOPE (Phase 5) |
| **Multi-Venue Execution** | Hyperliquid API integration (perp DEX) | Phase 4 completion | 🟢 IN SCOPE (Phase 6) |
| **On-Chain Liquidity** | Uniswap v4 Dynamic Fee Hooks (regime-based fees) | External audit + Phase 5 & 6 stability | 🟢 IN SCOPE (Phase 7) |