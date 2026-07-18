# Product Requirements Document (PRD)
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Draft / Approved
**Last Revised:** 2026-07-17 — WARP→WireGuard cleanup
**Target Release:** V1.0 (Paper Trading) -> V1.1 (Live Capital)  

---

## 1. Executive Summary
`karsa-auto-session-manager` is an institutional-grade, autonomous cryptocurrency trading system designed to exploit structural market inefficiencies across the broader crypto ecosystem. Unlike traditional bots that rely on single-exchange data, this system operates on a **"Read Global, Execute Local"** paradigm. It ingests real-time, aggregated market data from the top global exchanges via CCXT to establish the "true" market state, and executes directional trades exclusively on Bybit. 

Due to regional geo-restrictions, Bybit execution must be routed through a WireGuard VPN via gluetun sidecar. To mitigate the latency introduced by this proxy, the system's strategic timeframe has been deliberately pivoted from High-Frequency Trading (HFT) to **Intraday and Swing trading (15m - 4h charts)**, focusing on macro structural imbalances rather than millisecond price lags.

---

## 2. Problem Statement
1. **Single-Exchange Blindness:** Trading bots that only read data from their execution venue (e.g., only looking at Bybit) are blind to global liquidity shifts, leading to poor entries and vulnerability to exchange-specific "scam wicks."
2. **The Latency/Geo-Block Paradox:** To access Bybit, a proxy (WireGuard VPN) is required, adding 100ms–300ms of latency. Attempting HFT or millisecond lead-lag scalping through a proxy guarantees negative alpha due to slippage and market-maker front-running.
3. **State Divergence in Microservices:** Traditional distributed bot architectures (separate signal generators and executors communicating via message queues) introduce internal network latency and state-sync failures, which are fatal when external proxy latency is already high.

---

## 3. Core Thesis & Strategic Pivot

### The Core Thesis: "Read Global, Execute Local"
The system treats Bybit strictly as an **execution venue**, not a data source for alpha. 
* **The Read Pipeline (Global):** Uses CCXT Pro WebSockets to ingest L2 order books, trades, and funding rates from Binance, OKX, and Bybit. It calculates a unified "Global State" (Global VWAP, Aggregate Order Book Skew, Average Funding).
* **The Write Pipeline (Local):** Executes trades *only* on Bybit. The system uses the Global State as a leading indicator. If the global aggregate shifts heavily bullish, but Bybit's local price hasn't moved yet, the system executes a Long on Bybit, anticipating that Bybit's price will inevitably be pulled up by global market forces.

### The Strategic Pivot: Intraday & Swing over HFT
Because the mandatory WireGuard VPN tunnel introduces unavoidable execution latency, the system **will not** compete on speed. 
* **Abandoned:** Millisecond lead-lag scalping, 1-minute chart HFT, and order-book queue positioning.
* **Adopted:** Intraday (15m/1h) and Swing (4h) timeframes. The system captures *structural* and *macro* inefficiencies (e.g., global funding rate divergences, macro open-interest shifts) that take minutes or hours to play out, rendering the 200ms proxy latency mathematically irrelevant to the trade's profitability.

---

## 4. Target Markets
* **Asset Class:** USDT-Margined Perpetual Futures.
* **Universe:** The **Top 20 most liquid assets** by 24h volume (e.g., BTC, ETH, SOL, BNB, XRP, DOGE, etc.). 
* **Selection Criteria:** Assets must have deep, continuous order books across *all* major exchanges to ensure the "Global State" calculations are accurate and not skewed by low-liquidity anomalies. Low-cap altcoins are strictly excluded.

---

## 5. Expected Outcomes & Alpha Sources
The system is expected to generate positive risk-adjusted returns (Alpha) by capturing the following specific market inefficiencies:

1. **Global Funding Rate Divergence:** Identifying when the aggregate global funding rate is heavily skewed (e.g., extreme short interest globally), but Bybit's local funding is neutral, indicating a high-probability macro squeeze.
2. **Structural Order Book Imbalance:** Detecting when the aggregated bid/ask depth across Binance and OKX heavily favors one side, creating a magnetic pull on the price that Bybit will eventually follow.
3. **Macro Lead-Lag Trends:** Using the higher-timeframe (15m+) price action of the "Price Leader" (usually Binance) to confirm trend direction before entering a position on Bybit.
4. **Session-Based Volatility Capture:** Utilizing the Session Orchestrator to apply specific strategies based on UTC time blocks (e.g., mean-reversion during the Asian session, momentum breakout during the NY session).

---

## 6. Key System Requirements (6-Stage Trade Lifecycle)

The system follows a 6-stage pipeline. AI is **mandatory** in two safe positions (pre-entry analyst + post-entry position judge), strictly forbidden in the execution hot path.

1. **Universe Selection (Dynamic Scoring):** Score all configured symbols by Volume + Momentum + Squeeze + Overextension. Top 15 above threshold, respecting sector diversity cap (max 2 per sector). Cross-exchange aggregate volume from Binance+OKX+Bybit.
2. **Regime Detection:** Classify BTC market regime every 15 minutes using Hurst Exponent + ADX(14) + EMA(200). CHOP regime halts all trading. TREND_BULL/BEAR and MEAN_REVERSION apply regime-specific confidence modifiers.
3. **Signal Generation (AI-Mandatory):** Multi-signal composite confidence (skew + lead-lag + funding + OI). Entry filter (5 checks). Multi-timeframe confirmation (4H EMA trend). **AI CryptoAnalyst via 9router** synthesizes all signals into final confidence. If AI fails, signal is rejected.
4. **Risk Gate (Deterministic):** 3-layer gate (liquidity, spread health, circuit breaker) + sector diversity cap. No AI in this path.
5. **SOR Execution (Deterministic):** Post-Only Limit → Reprice → Market IOC on Bybit. Exchange-side SL placed immediately on fill. No AI in this path.
6. **Post-Entry Management (AI-Mandatory):** Trailing stop (ATR-based). Checkpoint manager (HARD_FAIL, CLEAR_WIN, TIME_STOP). **AI Position Judge** in ambiguous zone (2-tier escalation). 3 consecutive HOLDs on loser = forced EXIT. Trade memory stored for future AI context.

---

## 7. Infrastructure & Technical Constraints
* **Architecture:** **Single-Process Python Application.** To mitigate internal network latency and state divergence, the Orchestrator and Bot are merged into one `asyncio` event loop.
* **Proxy Mandate:** All Bybit REST and WebSocket traffic *must* be routed through the WireGuard VPN tunnel (gluetun sidecar).
* **LLM Integration (9router):** AI is **mandatory** in two safe positions: pre-entry CryptoAnalyst and post-entry PositionJudge, via 9router proxy at `127.0.0.1:20129`. AI is **strictly forbidden** in the execution hot path (SOR/risk gate). Models: `claude-haiku-3-5` for analyst and cheap judge, `claude-sonnet-4-5` for escalated judge. See `docs/review/ai_layer_analysis.md`.
* **Environment:** Local development and paper trading will be containerized via Docker Compose (App, Redis, Postgres, Prometheus, Grafana).

---

## 8. Out of Scope for V1 (Strict Boundaries)
To ensure rapid deployment and capital preservation, the following are explicitly **OUT OF SCOPE** for Version 1:
* Multi-exchange execution (arbitrage). The bot only trades on Bybit.
* High-Frequency Trading (HFT) or sub-second scalping strategies.
* Using the LLM in the execution hot path (SOR/risk gate). AI is mandatory in safe positions only (pre-entry analyst, post-entry judge).
* Complex Reinforcement Learning (RL) execution models (e.g., FinRL).
* Trading low-liquidity altcoins or spot markets.

---

## 9. Success Metrics
* **System Stability:** 99.9% uptime during active trading sessions. Zero state-divergence incidents between local DB and Bybit.
* **Execution Quality:** Average execution latency (from signal generation to Bybit fill confirmation) must remain under 800ms (accounting for VPN tunnel overhead).
* **Risk Management:** Maximum daily drawdown strictly capped at 2%. Zero instances of the bot opening a trade that violates the 3-Layer Risk Gate.
* **Profitability (Paper):** Achieve a positive Sharpe Ratio (> 1.5) and a win rate > 52% over a 30-day continuous paper-trading period before live capital deployment.