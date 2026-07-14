# Product Requirements Document (PRD)
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Draft / Approved  
**Target Release:** V1.0 (Paper Trading) -> V1.1 (Live Capital)  

---

## 1. Executive Summary
`karsa-auto-session-manager` is an institutional-grade, autonomous cryptocurrency trading system designed to exploit structural market inefficiencies across the broader crypto ecosystem. Unlike traditional bots that rely on single-exchange data, this system operates on a **"Read Global, Execute Local"** paradigm. It ingests real-time, aggregated market data from the top global exchanges via CCXT to establish the "true" market state, and executes directional trades exclusively on Bybit. 

Due to regional geo-restrictions, Bybit execution must be routed through a Cloudflare WARP (SOCKS5) proxy. To mitigate the latency introduced by this proxy, the system's strategic timeframe has been deliberately pivoted from High-Frequency Trading (HFT) to **Intraday and Swing trading (15m - 4h charts)**, focusing on macro structural imbalances rather than millisecond price lags.

---

## 2. Problem Statement
1. **Single-Exchange Blindness:** Trading bots that only read data from their execution venue (e.g., only looking at Bybit) are blind to global liquidity shifts, leading to poor entries and vulnerability to exchange-specific "scam wicks."
2. **The Latency/Geo-Block Paradox:** To access Bybit, a proxy (WARP) is required, adding 100ms–300ms of latency. Attempting HFT or millisecond lead-lag scalping through a proxy guarantees negative alpha due to slippage and market-maker front-running.
3. **State Divergence in Microservices:** Traditional distributed bot architectures (separate signal generators and executors communicating via message queues) introduce internal network latency and state-sync failures, which are fatal when external proxy latency is already high.

---

## 3. Core Thesis & Strategic Pivot

### The Core Thesis: "Read Global, Execute Local"
The system treats Bybit strictly as an **execution venue**, not a data source for alpha. 
* **The Read Pipeline (Global):** Uses CCXT Pro WebSockets to ingest L2 order books, trades, and funding rates from Binance, OKX, Bybit, and Coinbase. It calculates a unified "Global State" (Global VWAP, Aggregate Order Book Skew, Average Funding).
* **The Write Pipeline (Local):** Executes trades *only* on Bybit. The system uses the Global State as a leading indicator. If the global aggregate shifts heavily bullish, but Bybit's local price hasn't moved yet, the system executes a Long on Bybit, anticipating that Bybit's price will inevitably be pulled up by global market forces.

### The Strategic Pivot: Intraday & Swing over HFT
Because the mandatory WARP proxy introduces unavoidable execution latency, the system **will not** compete on speed. 
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

## 6. Key System Requirements (The "6 Keys")

1. **Global Read Engine (CCXT Pro):** Maintain persistent, fault-tolerant WebSocket connections to 3-5 major exchanges. Normalize and aggregate data into a unified in-memory state.
2. **Session Orchestrator:** Manage trading logic based on UTC time blocks (Asia, London, NY) and dynamically adjust strategy parameters based on the current market regime.
3. **Alpha Bridge:** Calculate the mathematical edge (Global Skew, Funding Divergence, VWAP deviations) and generate raw directional signals.
4. **Local Execution Engine (Bybit):** Execute trades strictly on Bybit via **Private WebSockets** (to minimize proxy latency). Implement local Smart Order Routing (Post-Only Limit -> Reprice -> Market).
5. **Globalized 9-Layer Risk Gate:** A deterministic, rule-based risk engine that evaluates signals against global liquidity, cross-exchange spread, and portfolio correlation before allowing execution.
6. **Telemetry & State Reconciliation:** Expose metrics to Prometheus/Grafana. Implement a strict startup reconciliation process to sync local Postgres state with actual Bybit positions.

---

## 7. Infrastructure & Technical Constraints
* **Architecture:** **Single-Process Python Application.** To mitigate internal network latency and state divergence, the Orchestrator and Bot are merged into one `asyncio` event loop.
* **Proxy Mandate:** All Bybit REST and WebSocket traffic *must* be routed through the Cloudflare WARP SOCKS5 proxy (`socks5h://host.docker.internal:1080`).
* **LLM Constraint (The 9 Router):** The Anthropic/DeepSeek LLM is **strictly prohibited from the hot execution path**. It will only be used in background threads for daily parameter tuning, regime detection, and post-trade journaling.
* **Environment:** Local development and paper trading will be containerized via Docker Compose (App, Redis, Postgres, Prometheus, Grafana).

---

## 8. Out of Scope for V1 (Strict Boundaries)
To ensure rapid deployment and capital preservation, the following are explicitly **OUT OF SCOPE** for Version 1:
* Multi-exchange execution (arbitrage). The bot only trades on Bybit.
* High-Frequency Trading (HFT) or sub-second scalping strategies.
* Using the LLM (9 Router) for real-time trade validation or entry/exit decisions.
* Complex Reinforcement Learning (RL) execution models (e.g., FinRL).
* Trading low-liquidity altcoins or spot markets.

---

## 9. Success Metrics
* **System Stability:** 99.9% uptime during active trading sessions. Zero state-divergence incidents between local DB and Bybit.
* **Execution Quality:** Average execution latency (from signal generation to Bybit fill confirmation) must remain under 800ms (accounting for WARP proxy overhead).
* **Risk Management:** Maximum daily drawdown strictly capped at 3%. Zero instances of the bot opening a trade that violates the 9-Layer Risk Gate.
* **Profitability (Paper):** Achieve a positive Sharpe Ratio (> 1.5) and a win rate > 52% over a 30-day continuous paper-trading period before live capital deployment.