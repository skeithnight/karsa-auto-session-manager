# End-to-End Workflow
**Project:** `karsa-auto-session-manager`
**Last Updated:** 2026-07-22

---

## Overview

Single-process asyncio bot. Reads Binance/OKX/Bybit, executes Bybit-only via WireGuard VPN. 15m–4h swing. AI via 9router (mandatory in safe positions only).

Two entry points exist:
- **`app/main.py`** — original monolith, async tasks + `asyncio.Queue`
- **`app/consumer/live_loop.py`** — newer consumer-based architecture (live mode)
- **`app/consumer/shadow_loop.py`** — shadow mode variant

---

## Full Pipeline (6 Stages)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: DATA INGESTION                          │
│                                                                     │
│  Binance WS ─┐                                                      │
│  OKX WS ─────┼→ CCXTManager → Normalizer → BadTickFilter           │
│  Bybit WS ───┘      │              │              │                 │
│                      │         ExchangeData    filtered ticks        │
│                      ▼              ▼              ▼                 │
│              MarketDataIngestor → Redis `global:state:{symbol}`     │
│              (app/data/market_data_ingestor.py)                      │
│                      │                                               │
│              Also writes:                                            │
│              - L2 orderbook snapshots                                │
│              - Funding rates                                         │
│              - Open interest                                         │
│              - Heartbeats → `system:heartbeats`                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                STAGE 2: REGIME DETECTION                             │
│                                                                     │
│  RegimeClassifier (app/alpha/regime_classifier.py)                  │
│  Runs every 15 min on BTC/USDT 1H candles (200 bars)               │
│                                                                     │
│  Indicators:                                                        │
│  - Hurst Exponent (R/S, windows 10/20/40) → H>0.55 trend           │
│  - ADX(14) → >25 strong trend, <20 chop                             │
│  - ATR percentile → volatility context                              │
│                                                                     │
│  Output: MarketRegime enum                                          │
│  - TREND_BULL → momentum strategies                                 │
│  - TREND_BEAR → momentum strategies                                 │
│  - RANGE → mean-reversion strategies                                │
│  - CHOP → HALT all trading                                          │
│                                                                     │
│  Stored: Redis `system:config:regime`                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│             STAGE 3: SIGNAL GENERATION (AI-Mandatory)               │
│                                                                     │
│  Sequential pipeline (DecisionEngine / alpha_bridge_task):          │
│                                                                     │
│  3a. StrategyRouter (app/alpha/strategy_router.py)                  │
│      Hub-and-Spoke: regime-specific confidence scoring              │
│      - TREND: momentum + breakout + global sync                     │
│      - RANGE: BB edge-fade + wick rejection                         │
│      - CHOP: orderbook liquidity sweep + funding extremes           │
│      Output: 0–100 confidence score                                 │
│                                                                     │
│  3b. EntryFilter (app/alpha/entry_filter.py)                        │
│      5 checks:                                                      │
│      - Regime != CHOP                                               │
│      - Spread < 0.3%                                                │
│      - Depth ratio balanced                                         │
│      - Time-of-day (block 00:00–01:00 UTC)                          │
│      - No duplicate position                                        │
│                                                                     │
│  3c. MultiTFFilter (app/alpha/multi_tf.py)                          │
│      - 4H EMA(20) trend check → 0.5x penalty if fighting            │
│      - Macro Anchor (BTC/ETH) → 0.8x penalty if contradicting       │
│      - Momentum exemption (>15% 24h move bypasses penalties)         │
│                                                                     │
│  3d. CryptoAnalyst (app/alpha/analyst.py) — MANDATORY               │
│      - Fetches 200 1H candles → computes TA (RSI, BB, MACD, ATR)    │
│      - Injects TradeMemory (last 3 similar trades from Redis)       │
│      - Sends structured prompt to 9router (claude-haiku-3-5)        │
│      - Final confidence = quant × 0.5 + ai × 0.5                    │
│      - Gate: final_confidence >= 0.65                                │
│      - AI failure → signal REJECTED (not bypassed)                  │
│                                                                     │
│  Output: TradeSignal (symbol, side, confidence, regime, metadata)   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│            STAGE 4: RISK GATE (Deterministic Only)                  │
│                                                                     │
│  Sequential gates (PortfolioRiskManager → RiskGate → SectorCap):    │
│                                                                     │
│  4a. PortfolioRiskManager (app/risk/portfolio_risk_manager.py)      │
│      - Correlation trap: max 2 positions per sector                 │
│      - Gross exposure limit (notional vs equity)                    │
│      - Net exposure limit (directional bias)                        │
│      - Circuit breaker state check                                  │
│                                                                     │
│  4b. RiskGate (app/risk/gates.py)                                   │
│      - Circuit breaker: is_halted() / is_paused()                   │
│      - Liquidity: 24h volume >= $1M                                 │
│      - Spread health: bid-ask <= 0.5%                               │
│                                                                     │
│  4c. SectorCap (app/risk/sector_cap.py)                             │
│      - Max 2 positions per sector (BTC/ETH, L1, L2, DeFi, etc.)    │
│                                                                     │
│  4d. CircuitBreaker (app/risk/circuit_breaker.py)                   │
│      - Daily portfolio loss >= 2% → HALT                            │
│      - 4 consecutive losses → SOFT STOP (60 min)                    │
│                                                                     │
│  All pass → signal queued for execution                             │
│  Any fail → signal BLOCKED, logged                                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│          STAGE 5: SOR EXECUTION (Deterministic Only)                │
│                                                                     │
│  SmartOrderRouter (app/execution/sor.py)                            │
│  Via WireGuard VPN → Bybit REST/WS                                  │
│                                                                     │
│  5a. Iceberg Slicing (notional > $2,000)                            │
│      - 4 hidden chunks, randomized 1.5–3.5s delays                  │
│                                                                     │
│  5b. Post-Only Limit at current price                               │
│                                                                     │
│  5c. Adaptive Reprice (up to 2 attempts)                            │
│      - If spread widens > 0.2% → drop delay to 100ms                │
│                                                                     │
│  5d. Market/IOC fallback                                            │
│                                                                     │
│  5e. Exchange-side SL placed IMMEDIATELY on fill                     │
│      - via bybit_client.place_stop_loss()                           │
│      - No AI in this path                                           │
│                                                                     │
│  5f. Position registered in PositionStore (Redis) + TradeStore (PG) │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│         STAGE 6: POST-ENTRY MANAGEMENT (APM Loop)                   │
│                                                                     │
│  ActivePositionManager (app/execution/position_manager.py)          │
│  2-second async loop, try/except + sleep on error                   │
│                                                                     │
│  6a. R-Multiple Tracking                                            │
│      LONG:  (live_price - entry) / initial_risk_per_unit            │
│      SHORT: (entry - live_price) / initial_risk_per_unit            │
│                                                                     │
│  6b. +1R Breakeven Lock (universal)                                 │
│      When R >= 1.0 for first time:                                  │
│      - Move exchange-side SL to entry ± 0.1% fee buffer             │
│      - RANGE/CHOP: also close 50% at market                         │
│                                                                     │
│  6c. Regime-Specific Management                                     │
│  ┌─────────────┬──────────────────┬──────────┬──────────┬─────────┐ │
│  │ Regime      │ SL Style         │ TP Style │ Time Exit│ Trailing│ │
│  ├─────────────┼──────────────────┼──────────┼──────────┼─────────┤ │
│  │ TREND       │ 1x ATR swing low │ None     │ 24h      │ 3x ATR  │ │
│  │ RANGE       │ 0.1-0.2% BB edge │ Opp edge │ 4h       │ None    │ │
│  │ CHOP        │ Beyond local wick│ 1:1 R:R  │ 30min    │ None    │ │
│  └─────────────┴──────────────────┴──────────┴──────────┴─────────┘ │
│                                                                     │
│  6d. Regime Shift Kill Switch                                       │
│      Every cycle: current_regime != entry_regime → force close      │
│                                                                     │
│  6e. CheckpointManager (every 5 min)                                │
│      - HARD_FAIL: -2% in 30min or -3% ever → immediate exit         │
│      - CLEAR_WIN: gain > 3x ATR → activate trailing                │
│      - AMBIGUOUS → AI Position Judge (MANDATORY)                    │
│      - TIME_STOP: held > 72h → forced exit                          │
│                                                                     │
│  6f. AI Position Judge (app/alpha/position_judge.py)                │
│      - 2-tier: haiku (cheap) → sonnet (escalated)                   │
│      - 3 consecutive HOLDs on loser → forced EXIT                   │
│      - Returns: EXIT / HOLD / TIGHTEN_STOP                          │
│                                                                     │
│  6g. TradeMemory (app/alpha/trade_memory.py)                        │
│      On exit: store PnL, hold_duration, regime, exit_reason         │
│      to Redis sorted set for future AI context                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Async Task Architecture (main.py)

```
main()
  │
  ├── data_engine_task()          ← Always on
  │   ├── CCXTManager (WS feeds)
  │   ├── Normalizer
  │   ├── BadTickFilter
  │   └── Writes to Redis global:state:{symbol}
  │
  ├── regime_task()               ← Always on, every 15 min
  │   └── RegimeClassifier → Redis system:config:regime
  │
  ├── alpha_bridge_task()         ← Always on
  │   ├── SignalGenerator (composite score)
  │   ├── EntryFilter (5 checks)
  │   ├── MultiTFFilter (4H + macro)
  │   ├── CryptoAnalyst (AI, mandatory)
  │   └── Output → asyncio.Queue signal_queue
  │
  ├── risk_gate_task()            ← Always on, reads signal_queue
  │   ├── PortfolioRiskManager
  │   ├── RiskGate (3-layer)
  │   ├── SectorCap
  │   └── Output → asyncio.Queue risk_queue
  │
  ├── executor_task()             ← Reads risk_queue
  │   ├── Duplicate position check (PositionStore)
  │   ├── SmartOrderRouter.execute()
  │   ├── Exchange-side SL placement
  │   └── Position registered in PositionStore + TradeStore
  │
  ├── active_position_manager()   ← Always on, 2s loop
  │   ├── R-multiple tracking
  │   ├── Breakeven lock
  │   ├── Regime shift kill switch
  │   ├── CheckpointManager
  │   └── AI Position Judge
  │
  ├── watchdog_task()             ← Always on
  │   ├── WS heartbeat monitor
  │   ├── Execution latency tracker
  │   ├── Event loop lag monitor
  │   └── Dead man's switch (HTTP ping)
  │
  ├── position_reconciler_task()  ← Every 5 min
  │   └── Compare internal state vs Bybit REST
  │
  ├── metrics_publisher_task()    ← Always on
  │   └── Prometheus /metrics endpoint
  │
  └── trade_reconciler_task()     ← Periodic
      └── Postgres audit trail sync
```

---

## Consumer-Based Architecture (live_loop.py)

The newer architecture separates concerns:

```
live_loop.py main()
  │
  ├── startup()                   ← Init Redis, Postgres, dependencies
  │
  ├── _start_ingestor()           ← MarketDataIngestor
  │   └── Feeds MarketConsumer
  │
  ├── MarketConsumer              ← Consumes normalized market data
  │   ├── Maintains GlobalState
  │   └── Feeds DecisionEngine
  │
  ├── DecisionEngine              ← Full signal pipeline
  │   ├── RegimeClassifier
  │   ├── StrategyRouter
  │   ├── EntryFilter
  │   ├── MultiTFFilter
  │   ├── CryptoAnalyst
  │   └── Outputs TradeSignal
  │
  ├── PortfolioRiskManager        ← Pre-trade gate
  ├── DynamicRiskGate             ← Regime-specific risk profiles
  ├── SmartOrderRouter            ← Execution
  ├── ActivePositionManager       ← Post-entry
  │
  ├── StateReconciler             ← Startup reconciliation
  ├── TelemetryEmitter            ← Prometheus metrics
  │
  └── Watchdog                    ← Health monitoring
```

---

## Shadow Mode (shadow_loop.py)

Same pipeline as live, with component substitution:

| Live Component | Shadow Replacement |
|---|---|
| `SmartOrderRouter` | `ShadowExecutor` (simulated SOR, asymmetric fees) |
| `ActivePositionManager` | `ShadowAPM` (wick miss, funding drag, pending fills) |
| `BybitClient` | `ShadowExchangeClient` (Redis-backed mock) |
| `PositionStore` | `ShadowPositionStore` (`shadow:position:*` Redis keys) |
| `TradeStore` | `ShadowTradeStore` (`shadow_trades` PG table) |

Shadow mode skips startup reconciliation and position_reconciler.

---

## Data Flow Summary

```
Exchanges (WS)
    │
    ▼
CCXTManager → Normalizer → BadTickFilter
    │
    ▼
Redis global:state:{symbol}  ← Single source of truth for market data
    │
    ├──→ RegimeClassifier → Redis system:config:regime
    │
    ├──→ StrategyRouter → EntryFilter → MultiTFFilter → CryptoAnalyst
    │                                                      │
    │                                          TradeSignal (conf >= 0.65)
    │                                                      │
    ▼                                                      ▼
PortfolioRiskManager → RiskGate → SectorCap → CircuitBreaker
                                                    │
                                                    ▼ (all pass)
                                            SmartOrderRouter
                                                    │
                                                    ▼ (fill)
                                            PositionStore + TradeStore
                                                    │
                                                    ▼
                                            ActivePositionManager
                                                    │
                                                    ├──→ Breakeven Lock
                                                    ├──→ Trailing Stop
                                                    ├──→ Regime Shift Kill
                                                    ├──→ CheckpointManager
                                                    └──→ Position Judge AI
```

---

## Redis Key Map

| Key Pattern | Purpose | Writer |
|---|---|---|
| `global:state:{symbol}` | Aggregated market snapshot (price, volume, skew, funding) | MarketDataIngestor |
| `system:config:regime` | Current market regime (TREND_BULL/BEAR/RANGE/CHOP) | RegimeClassifier |
| `system:heartbeats` | Per-exchange WS heartbeat timestamps | MarketDataIngestor |
| `system:universe:symbols` | Top N scored symbols (refreshed every 4h) | UniverseScorer |
| `system:circuit_breaker` | Circuit breaker state | CircuitBreaker |
| `position:{symbol}:{side}` | Active position state | PositionStore |
| `shadow:position:{symbol}:{side}` | Shadow position state | ShadowPositionStore |
| `ai:cache:*` | Cached AI analyst results (5min TTL) | CryptoAnalyst |
| `trade:memory:{symbol}:{regime}` | Last 3 similar trades for AI context | TradeMemory |

---

## Key Files Reference

| File | Role |
|---|---|
| `app/main.py` | Original monolith entry point |
| `app/consumer/live_loop.py` | Newer consumer-based entry (live) |
| `app/consumer/shadow_loop.py` | Shadow mode entry |
| `app/consumer/decision_engine.py` | Full signal pipeline orchestrator |
| `app/consumer/market_consumer.py` | Market data consumer |
| `app/data/market_data_ingestor.py` | CCXT WS → Redis |
| `app/data/normalizer.py` | Exchange schema normalization |
| `app/data/filters.py` | Bad tick rejection |
| `app/data/universe_scorer.py` | Dynamic symbol scoring |
| `app/alpha/regime_classifier.py` | Regime detection (Hub) |
| `app/alpha/strategy_router.py` | Regime-specific scoring (Spokes) |
| `app/alpha/signals.py` | Multi-signal composite |
| `app/alpha/entry_filter.py` | Pre-entry checklist |
| `app/alpha/multi_tf.py` | Multi-timeframe + macro anchor |
| `app/alpha/analyst.py` | AI pre-entry analyst |
| `app/alpha/position_judge.py` | AI position judge |
| `app/alpha/trade_memory.py` | Trade history for AI |
| `app/risk/portfolio_risk_manager.py` | Pre-trade portfolio gate |
| `app/risk/gates.py` | 3-layer risk gate |
| `app/risk/circuit_breaker.py` | Session-level circuit breaker |
| `app/risk/sector_cap.py` | Sector diversity cap |
| `app/risk/dynamic_risk_gate.py` | Regime-specific risk profiles |
| `app/execution/sor.py` | Smart Order Router |
| `app/execution/bybit_client.py` | Bybit REST/WS client |
| `app/execution/position_manager.py` | Active Position Manager |
| `app/execution/position_lifecycle.py` | Trailing stop + checkpoints |
| `app/execution/shadow.py` | Shadow executor + APM |
| `app/watchdog/monitor.py` | Health monitoring |
| `app/watchdog/dead_mans_switch.py` | External health ping |
| `app/core/state.py` | State manager (Postgres sync) |
| `app/core/position_store.py` | Redis position lifecycle |
| `app/core/trade_store.py` | Postgres trade CRUD |
| `app/core/ai_client.py` | 9router HTTP client |
| `app/core/config.py` | Pydantic Settings |
