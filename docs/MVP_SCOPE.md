# Minimum Viable Product (MVP) Scope & Implementation Status
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Executive Summary & Delivery Objective

The primary objective of Karsa ASM is to operate an autonomous, institutional-grade, risk-managed crypto trading application. The system operates on a single-process core loop per trading mode (`karsa-live`, `karsa-shadow`) with isolated helper container services.

All strategic pivots (15m-4h swing focus, mandatory exchange-side Stop Loss, strict `decimal.Decimal` numeric types, DoH network bypass, Hub-and-Spoke regime classification, EV composite scoring, mandatory safe-point AI evaluation, and pre-trade portfolio risk management) are fully integrated into the live codebase.

---

## 2. In-Scope System Component Verification

### A. Infrastructure & Network Security
- **Single-Process Core Engine**: `app/main.py` runs as a unified `asyncio` monolith per execution container (`karsa-live` / `karsa-shadow`), eliminating internal IPC latency.
- **Multi-Container Topology**: Isolated containers for `karsa-live`, `karsa-shadow`, `karsa-data-engine`, `karsa-commander`, `karsa-backtest`, `karsa-9router`, `karsa-postgres`, and `karsa-redis`.
- **DoH Network Bypass (`app/core/dns_bypass.py`)**: Cloudflare DoH (`1.1.1.1/dns-query`) patches `socket.getaddrinfo` on startup, resolving exchange APIs in ~50ms despite ISP DNS poisoning.
- **State Reconciliation**: Startup reconciliation (`state_reconciliation.py`) verifies in-memory, Postgres, and Bybit open position states before trading resumes.

### B. Market Data & On-Chain Feed (The "Read" Pipeline)
- **CCXT Pro WebSockets**: Real-time tick & orderbook streaming from Bybit.
- **Data Normalization & Anomaly Filtering**: `Normalizer` standardizes exchange payloads; `BadTickFilter` rejects tick spikes (> 5% in < 1s).
- **On-Chain DEX Alpha Feed (`app/data/onchain_feed.py`)**: EVM slot0 pool price polling published to Redis `onchain:symbol:{symbol}` (`ex=30`).

### C. Alpha & Signal Engine (The "Brain")
- **The Hub (`RegimeClassifier`)**: Computes ADX(14), Hurst Exponent (R/S), and ATR percentile to classify regimes into `TREND_BULL`, `TREND_BEAR`, `RANGE`, `CHOP`.
- **Multi-Resolution (`MultiResolutionRegimeClassifier`)**: Matches strategy holding periods across 15m, 1h, and 4h timeframes.
- **The Spokes (`StrategyRouter`)**: Regime-specific scoring spokes combined with composite signal formulas (skew, funding, open interest, `LeadLagBuffer`).
- **EV Composite Scoring (`EVScorer`)**: 9 weighted components + DEX divergence adjustment (+0.05 max EV) + `EVThreshold` dynamic thresholding.
- **Mandatory AI Pre-Entry Analyst (`CryptoAnalyst`)**: Prompts `9router` proxy (`http://karsa-9router:20129`) with TA context and `TradeMemory`. Times out/fails safely to `0.0` confidence (reject).

### D. Portfolio Risk Management (The "Shield")
- **Portfolio Risk Manager (`PortfolioRiskManager`)**:
  - Sector Correlation Cap (max 2 open positions per sector).
  - Gross Exposure Cap (% of equity).
  - Net Directional Exposure Cap.
  - Systemic Daily `CircuitBreaker` (-2% daily account loss stop).
- **Dynamic Risk Gate (`DynamicRiskGate`)**: Regime-specific risk profiles and CHOP sub-strategy gating.

### E. Execution & Active Position Management (The "Hands")
- **Smart Order Router (`SmartOrderRouter`)**: Post-Only -> Reprice -> Market fallback.
- **Mandatory Exchange-Side Stop Loss**: `BybitClient` places hard Stop Loss on Bybit exchange server immediately upon fill.
- **Active Position Manager (`ActivePositionManager`)**: Continuous async loop enforcing 5% hard SL cap, breakeven lock at +1R, orphan minimum clean-up (< 5 USDT), regime shift kill switch, and post-entry `PositionJudge` AI review.

### F. Bot Interface & Telemetry
- **Single-Owner Command Interface (`karsa-commander`)**: Dedicated container running `run_bot` single-owner Telegram polling, eliminating long-polling conflicts.
- **Metrics & Watchdog**: Prometheus metrics endpoint (`/metrics`) and `Watchdog` health monitoring.