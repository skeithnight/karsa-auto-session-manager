# Product Requirements Document (PRD)
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Executive Summary
`karsa-auto-session-manager` (Karsa ASM) is an institutional-grade, autonomous cryptocurrency trading platform designed to capture structural inefficiencies across global crypto derivatives and decentralized exchanges (DEXs). Operating under a **"Read Global, Execute Local"** paradigm, the system ingests aggregated real-time market data from global venues (Binance, OKX, Bybit) and EVM pool slot0 prices, executing directional linear perpetual trades exclusively on Bybit.

---

## 2. Strategic Positioning & Core Thesis

### The Core Thesis: "Read Global, Execute Local"
Bybit is treated as a dedicated execution venue rather than an isolated island of market data:
- **Global Read Pipeline**: Normalizes orderbook depth, funding rates, open interest, and DEX pool prices to establish true market consensus.
- **Local Execution Pipeline**: Executes directional orders on Bybit perpetuals. If global aggregated metrics and DEX prices show strong bullish divergence while local Bybit price trails, Karsa ASM enters a long position anticipating local price alignment.

### Strategic Pivot: Intraday & Swing over HFT
- Execution via proxy environments introduces latency. Karsa ASM operates on **15m to 4h timeframes**, prioritizing macro structural edges, statistical expected value (EV), and regime alignment over sub-second speed.

---

## 3. Product Features & Pipeline Architecture

1. **DoH Network Bypass Layer**: Standard-library Cloudflare DoH (`https://1.1.1.1/dns-query`) eliminates ISP UDP 53 DNS poisoning in container environments without third-party libraries.
2. **Hub-and-Spoke Regime Strategy**:
   - **The Hub (`RegimeClassifier`)**: ADX(14) + Hurst Exponent (R/S) + ATR percentile classifies regimes (`TREND_BULL`, `TREND_BEAR`, `RANGE`, `CHOP`). CHOP immediately halts new signal generation.
   - **The Spokes (`StrategyRouter`)**: Applies strategy rules customized per regime across 15m/1h/4h timeframes.
3. **EV Composite Scoring & On-Chain DEX Alpha**:
   - Evaluates 9 weighted factors (`EVScorer`).
   - Applies DEX divergence adjustments up to `+0.05` EV when DEX slot0 prices lead CEX pricing.
   - Adjusts base EV threshold ($0.55$) dynamically (`EVThreshold`).
4. **Mandatory AI Pre-Entry Analyst (`CryptoAnalyst`)**:
   - Synthesizes quantitative indicators and historical trade outcomes (`TradeMemory`) via the local `9router` proxy (`http://karsa-9router:20129`).
   - If AI fails or times out, confidence drops to `0.0`, ensuring deterministic rejection.
5. **Portfolio Risk Gate (`PortfolioRiskManager`)**:
   - Pre-trade filter enforcing correlation caps (max 2 positions per sector), gross/net exposure caps, and daily system `CircuitBreaker` limits.
6. **Smart Order Routing & Active Position Management (APM)**:
   - `SmartOrderRouter`: Post-Only -> Reprice -> Market fallback.
   - Mandatory Exchange-Side Stop Loss placed on Bybit immediately upon fill.
   - `ActivePositionManager`: Hard 5% SL cap, breakeven lock at +1R, orphan minimum clean-up (< 5 USDT), regime shift kill switch (exit on shift), and post-entry `PositionJudge` AI evaluation.
7. **Single-Owner Command Interface**:
   - `karsa-commander`: Single-owner Python Telegram Bot polling (`run_bot`), eliminating polling conflicts.

---

## 4. Technical Stack & Infrastructure Boundaries

- **Execution Engine**: Single-process Python `asyncio` loop running inside `karsa-live` and `karsa-shadow`.
- **Infrastructure Services**:
  - `karsa-postgres`: PostgreSQL async storage for audit trails, decision logs, and trade history.
  - `karsa-redis`: Real-time state cache, stream ingestion, and inter-container transport.
  - `karsa-9router`: Next.js OpenAI-compatible AI gateway.
  - `karsa-commander`: Telegram command handler and bulk backtesting interface.
  - `karsa-backtest`: Parallel backtesting worker process.
- **Data Precision**: Strict `decimal.Decimal` for all money, prices, sizes, and PnL.