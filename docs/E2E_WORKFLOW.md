# End-to-End Workflow Architecture
**Project:** `karsa-auto-session-manager`
**Last Updated:** 2026-08-11

---

## 1. Executive Summary

`karsa-auto-session-manager` (Karsa ASM) is an autonomous, institutional-grade crypto trading system. It operates as a high-reliability single-process engine packaged into a clean multi-container topology (`karsa-live`, `karsa-shadow`, `karsa-data-engine`, `karsa-commander`, `karsa-backtest`, `karsa-9router`, `karsa-postgres`, `karsa-redis`).

All numerical monetary values (prices, sizes, PnL) strictly use `decimal.Decimal`. Every position opened on Bybit receives an **exchange-side Stop Loss** immediately upon fill. The AI layer is mandatory in two safe evaluation points (pre-entry `CryptoAnalyst` and post-entry `PositionJudge`) via the local `9router` proxy.

---

## 2. Full 9-Stage Pipeline Topology

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: NETWORK & DATA INGESTION                    │
│                                                                         │
│  Standard-Library DoH Bypass (app/core/dns_bypass.py) → Cloudflare DoH   │
│  Bybit CCXT Pro WS → CCXTManager → Normalizer → BadTickFilter           │
│  EVM Slot0 DEX Feed → OnChainFeed → Redis (`onchain:symbol:{symbol}`)   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                STAGE 2: REGIME CLASSIFICATION (THE HUB)                 │
│                                                                         │
│  RegimeClassifier (app/alpha/regime_classifier.py)                      │
│  Calculates ADX(14) + Hurst Exponent (R/S) + ATR percentile            │
│  Multi-Resolution Regime (15m / 1h / 4h timeframes)                    │
│  Output Regimes: TREND_BULL | TREND_BEAR | RANGE | CHOP                 │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 STAGE 3: STRATEGY ROUTER (THE SPOKES)                   │
│                                                                         │
│  StrategyRouter (app/alpha/strategy_router.py)                          │
│  Applies regime-specific scoring spokes + LeadLagBuffer                 │
│  Composite Signal (skew + lead_lag + funding + OI)                      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             STAGE 4: EXPECTED VALUE (EV) COMPOSITE SCORING              │
│                                                                         │
│  EVScorer (app/alpha/ev_scorer.py): 9 weighted components               │
│  DEX Divergence Adjustment: CEX vs DEX price lead-lag (+0.05 max EV)     │
│  EVThreshold: Base 0.55, dynamically adjusted for drawdown/session/streak│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              STAGE 5: MANDATORY AI PRE-ENTRY ANALYST                    │
│                                                                         │
│  CryptoAnalyst (app/alpha/analyst.py) via AIClient → 9router proxy      │
│  Fetches TA features + TradeMemory context                              │
│  AI failure/timeout → 0.0 confidence (guaranteed deterministic REJECT)   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  STAGE 6: PORTFOLIO RISK GATE                           │
│                                                                         │
│  PortfolioRiskManager (app/risk/portfolio_risk_manager.py)              │
│  1. Sector Correlation Cap (max 2 open per sector)                      │
│  2. Gross Exposure Cap (% of equity)                                    │
│  3. Net Directional Imbalance Cap                                       │
│  4. Daily Portfolio CircuitBreaker State                                │
│  5. DynamicRiskGate (Regime profiles & CHOP rules)                      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             STAGE 7: SMART ORDER ROUTING & EXECUTION                    │
│                                                                         │
│  SmartOrderRouter (app/execution/sor.py): Post-Only → Reprice → Market  │
│  BybitClient (app/execution/bybit_client.py): Instant Exchange SL       │
│  PositionStore (app/core/position_store.py): Redis state update         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            STAGE 8: ACTIVE POSITION MANAGER (APM LOOP)                  │
│                                                                         │
│  ActivePositionManager (app/execution/position_manager.py)             │
│  - Crash-safe async loop (try/except + sleep)                           │
│  - Mandatory 5% Hard SL Cap lock                                        │
│  - Breakeven lock at +1R profit                                         │
│  - Orphan order cleanup (< 5 USDT min)                                  │
│  - Mandatory Regime Shift Kill Switch (exit position on shift)          │
│  - PositionJudge 2-tier AI evaluation                                   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│          STAGE 9: TELEGRAM COMMAND BOT & SYSTEM TELEMETRY               │
│                                                                         │
│  karsa-commander: Single-owner PTB Telegram polling (/start, /dashboard)│
│  Watchdog & Telemetry: Prometheus metrics, DB/Redis reconciliation      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Stage-by-Stage Detailed Specification

### Stage 1: Network & Ingestion Layer
- **DoH DNS Bypass (`app/core/dns_bypass.py`)**: Standard-library Cloudflare DoH (`https://1.1.1.1/dns-query`) patches `socket.getaddrinfo` on startup. Bypasses ISP UDP 53 DNS poisoning in ~50ms without third-party dependencies.
- **CCXT Pro Feed (`app/data/ccxt_manager.py`)**: Manages WebSocket orderbook and tick streams.
- **Bad Tick Filtering (`app/data/filters.py`)**: Drops stale or corrupted price ticks before writing to state.
- **On-Chain DEX Feed (`app/data/onchain_feed.py`)**: Polls EVM Uniswap v3/v4 pool slot0 prices via RPC and publishes to Redis key `onchain:symbol:{symbol}` (`ex=30`).

### Stage 2: Regime Classification (The Hub)
- **`RegimeClassifier` (`app/alpha/regime_classifier.py`)**: Serves as the single source of truth for market state.
- **Metrics Computed**:
  - ADX(14): Quantifies trend strength.
  - Hurst Exponent (R/S): Measures mean-reverting ($H < 0.45$) vs trending ($H > 0.55$) behavior.
  - ATR Percentile: Determines volatility context.
- **Regime States**: `TREND_BULL`, `TREND_BEAR`, `RANGE`, `CHOP`. If `CHOP`, signal confidence is forced to `0.0`.

### Stage 3: Strategy Routing (The Spokes)
- **`StrategyRouter` (`app/alpha/strategy_router.py`)**: Routes candidate signals to regime-specific evaluation spokes.
  - **Trend Spokes**: Momentum, breakout validation, and multi-timeframe trend alignment.
  - **Range Spokes**: Bollinger Band edge-fade, RSI exhaustion, and wick rejection.
  - **Chop Spokes**: Liquidity sweeps and funding rate extremes.
- **Signal Composite (`app/alpha/signals.py`)**: Fuses orderbook imbalance, funding rate, open interest, and 15-min price velocity (`LeadLagBuffer`).

### Stage 4: Expected Value (EV) Composite Scoring
- **`EVScorer` (`app/alpha/ev_scorer.py`)**: Evaluates 9 weighted market components (RSI, Bollinger position, MACD, EMA distance, skew, CVD slope, depth ratio, funding rate, multi-TF agreement).
- **DEX Divergence Adjustment**: When DEX pool price leads Bybit CEX price in the trade direction, EV score receives up to a `+0.05` boost.
- **`EVThreshold` (`app/alpha/ev_threshold.py`)**: Dynamically shifts base threshold ($0.55$) based on current drawdown, session performance, and streak state.

### Stage 5: Mandatory AI Pre-Entry Gate
- **`CryptoAnalyst` (`app/alpha/analyst.py`)**: Mandated pre-entry review via `AIClient` calling the `9router` OpenAI-compatible proxy (`http://karsa-9router:20129`).
- **Trade Memory Injection**: Passes recent historical trade outcomes (`TradeMemory`) into the prompt.
- **Failure Safety**: If `9router` times out or returns an error, confidence drops to `0.0`, mathematically guaranteeing signal rejection.

### Stage 6: Pre-Trade Risk Gate
- **`PortfolioRiskManager` (`app/risk/portfolio_risk_manager.py`)**: Mandated pre-trade filter before order submission. Checks:
  1. Correlation Trap: Max 2 concurrent open positions per sector.
  2. Gross Exposure Limit: Total notional size vs account equity cap.
  3. Net Exposure Limit: Directional imbalance cap.
  4. Daily `CircuitBreaker`: Halts entries if daily drawdown limit is hit.
- **`DynamicRiskGate` (`app/risk/dynamic_risk_gate.py`)**: Enforces regime risk profiles.

### Stage 7: Smart Order Routing & Execution
- **`SmartOrderRouter` (`app/execution/sor.py`)**: Executes orders via Post-Only -> Reprice -> Market fallback, guarding against slippage.
- **`BybitClient` (`app/execution/bybit_client.py`)**: Places a hard exchange-side Stop Loss immediately upon fill.
- **`PositionStore` (`app/core/position_store.py`)**: Persists position lifecycle state in Redis.

### Stage 8: Active Position Management (APM Loop)
- **`ActivePositionManager` (`app/execution/position_manager.py`)**: Async loop running continuous position safety checks.
  - Hard SL Cap: Caps stop loss at maximum 5%.
  - Breakeven Lock: Locks Stop Loss to breakeven once price reaches +1R profit.
  - Orphan Minimum: Cleans up tiny residual positions (< 5 USDT notional).
  - Regime Shift Kill Switch: Market-closes positions immediately if regime changes unfavorably.
  - `PositionJudge` (`app/alpha/position_judge.py`): 2-tier post-entry AI evaluation.

### Stage 9: Telegram Command Bot & Telemetry
- **Single-Owner Polling (`karsa-commander`)**: `run_bot` runs exclusively inside the `karsa-commander` container to eliminate Telegram API long-polling conflicts.
- **Dashboard & Alerting**: Provides `/start`, `/dashboard`, `/status`, `/portfolio`, `/analytics`, and `/control` commands.
- **Telemetry**: Prometheus metrics, health heartbeats via `Watchdog`, and database reconciliation.
