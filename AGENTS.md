# AGENTS.md
**Project:** `karsa-auto-session-manager`
**Audience:** Codex / AI agents working in this repo.
**Read first:** `CONTEXT.md` (orientation + known doc conflicts). This file is rules, not background — go there for the "why."

---

## 1. Source-of-Truth Order

When docs disagree, resolve in this order. If the conflict is a **safety-critical numeric value** (thresholds, timeouts, %), do not pick one — stop and ask. See `CONTEXT.md` §7 for the currently known conflicts.

1. `docs/RISK_AND_RUNBOOK.md` — for anything runtime-safety related (kill switch, circuit breakers, failover, reconciliation behavior)
2. `docs/DEFINITION_OF_DONE.md` — for what "complete" means on any PR
3. `docs/DATA_MODEL.md` — for schemas, field names, types. **Never guess a field name or shape — if it's not in this doc, stop and ask rather than inventing it.**
4. `docs/ARCHITECTURE.md` — for structure, component boundaries, tech stack
5. `docs/MVP_SCOPE.md` — for what's actually in scope right now (overrides `docs/PRD.md` when they conflict on scope)
6. `docs/PRD.md` — vision/rationale only; treat as aspirational for anything not also in `docs/MVP_SCOPE.md`

**New adaptive-strategy docs (authoritative for Phase 6):**
- `docs/architecture/adaptive_multi_strategy.md` — RegimeClassifier math, StrategyRouter scoring
- `docs/execution/active_position_manager.md` — APM loop, Breakeven, Regime Shift Kill Switch
- `docs/risk/portfolio_risk_manager.md` — Correlation trap, exposure limits, CircuitBreaker

---

## 2. Non-Negotiable Rules

Straight from `docs/DEFINITION_OF_DONE.md` §4 ("Definition of NOT Done"). Violating any of these is an automatic reject, not a style nit.

| Rule | Detail |
| :--- | :--- |
| No `float` for money | Prices, sizes, PnL are always `decimal.Decimal`. `price = 64000.50` is wrong; `Decimal("64000.50")` is right. |
| No hardcoded secrets | API keys, Telegram tokens, DB passwords come from `.env` via Pydantic `Settings` — never inline, never in a default value. |
| No silent failures | `except: pass` is banned. Every caught exception either re-raises, degrades explicitly, or logs to Postgres/Telegram. |
| No blocking the event loop | `time.sleep()` and blocking `requests` calls are banned inside `asyncio` code. Use `await asyncio.sleep()` / async HTTP clients. |
| Every position gets an exchange-side SL | The Bybit Executor must place a hard Stop-Loss on the exchange server immediately on fill — not "eventually," not "if convenient." This is the single most safety-critical line of code in the repo. |
| No guessing field names | Use the strict Pydantic models in `docs/DATA_MODEL.md`. No raw `data['price']`-style dict access outside `app/data/normalizer.py`. |
| AI mandatory in safe positions | Pre-entry CryptoAnalyst and post-entry PositionJudge are not optional. LLM calls only via 9router proxy — never in the execution path (SOR/risk gate). See `docs/review/ai_layer_analysis.md`. |
| NEVER bypass `PortfolioRiskManager` | All entries MUST pass `PortfolioRiskManager` before `BybitExecutor` is called. Skipping it for convenience, testing, or "fast path" is an automatic reject. |
| All SL/TP must be exchange-side | Stop Loss and Take Profit MUST be placed/amended via Bybit API. Never rely on internal state alone for exits. If the process crashes, Bybit must still protect the position. |
| APM loops must be crash-safe | Every `ActivePositionManager` async loop MUST include `try/except` + `await asyncio.sleep()` on the error path. Infinite loops without sleep are CPU starvation bugs. |

---

## 3. Directory Map

```text
app/
├── main.py              # asyncio loop entrypoint — all 7 keys start here
├── core/
│   ├── config.py         # Pydantic Settings, loads .env — secrets live ONLY here
│   ├── database.py        # Postgres async pool (asyncpg)
│   ├── redis_client.py    # Redis async client (aioredis)
│   ├── session.py         # UTC session/regime logic (not one of the "6 Keys" — see CONTEXT.md #5)
│   ├── state.py            # In-memory state + Postgres sync (Key 5)
│   ├── trade_store.py      # Postgres trade CRUD
│   ├── ai_client.py        # 9router async HTTP client (AI layer)
│   ├── metrics.py          # Prometheus metrics (counters, gauges, histograms)
│   ├── position_store.py   # Redis-backed position lifecycle state
│   ├── shadow_store.py     # [BUILT] ShadowPositionStore + ShadowTradeStore (Phase 3.1)
│   ├── state_reconciliation.py  # Startup reconciliation (Postgres ↔ Bybit)
│   ├── dependencies.py     # Dependency injection container
│   ├── telemetry.py        # System telemetry collection
│   └── migrate.py          # DB migration runner
├── data/                  # Key 1 — Global Data Engine
│   ├── ccxt_manager.py     # CCXT Pro WS + load_markets() symbol validation
│   ├── normalizer.py       # ONLY place raw exchange dicts get touched directly
│   ├── filters.py            # Bad tick rejection
│   ├── ohlcv_fetcher.py      # Cached OHLCV REST fetcher
│   ├── universe_scorer.py    # Dynamic universe scoring (Volume+Momentum+Squeeze+Overextension)
│   ├── universe_scanner.py   # Periodic universe re-scan (new listings, delistings)
│   ├── market_data_ingestor.py  # Historical data ingestion for backtest
│   └── sector_mapping.py     # Static sector classification with keyword fallback
├── alpha/                  # Key 2 — Alpha Bridge (Hub-and-Spoke, Phase 6)
│   ├── metrics.py
│   ├── signals.py            # Multi-signal composite (skew+lead_lag+funding+OI)
│   ├── regime.py             # Hurst + ADX regime classifier (existing)
│   ├── regime_classifier.py  # [BUILT] RegimeClassifier — The Hub (ADX+Hurst+ATR, Phase 6)
│   ├── strategy_router.py    # [BUILT] StrategyRouter — The Spokes (per-regime scoring, Phase 6)
│   ├── lead_lag_buffer.py    # 15-min rolling price buffer
│   ├── entry_filter.py       # Pre-entry structural checklist (5 checks)
│   ├── ta_tools.py           # Deterministic TA indicators (RSI, BB, MACD, ATR, EMA)
│   ├── analyst.py            # AI pre-entry analyst (MANDATORY, via 9router)
│   ├── position_judge.py     # AI position judge (MANDATORY, 2-tier escalation)
│   ├── multi_tf.py           # Multi-timeframe confirmation (4H trend filter)
│   └── trade_memory.py       # Trade history injection for AI context
├── execution/               # Key 4 — Bybit Executor + APM (Phase 6)
│   ├── bybit_client.py       # Bybit REST/WS client + exchange-side SL
│   ├── sor.py                # Post-Only -> Reprice -> Market
│   ├── position_lifecycle.py # Trailing stop + performance checkpoints (existing)
│   ├── position_manager.py   # [BUILT] ActivePositionManager — 2s async loop (Phase 6)
│   └── shadow.py             # [BUILT] ShadowExecutor + ShadowAPM + ShadowExchangeClient (Phase 3.1)
├── risk/                     # Key 3 — Risk Gate (expanded, Phase 6)
│   ├── gates.py              # 3-Layer: liquidity, spread, circuit breaker
│   ├── circuit_breaker.py    # Per-session hard stop at -2% drawdown
│   ├── sector_cap.py         # Sector diversity cap (max 2 per sector)
│   ├── dynamic_risk_gate.py  # [BUILT] Regime-specific RiskProfile (Phase 6)
│   ├── portfolio_risk_manager.py  # [BUILT] Pre-trade: correlation, exposure, CB (Phase 6)
│   └── portfolio_hedge.py    # [PLANNED] Cross-portfolio delta hedge (post-MVP)
├── consumer/                 # Market data consumer (live + shadow loops)
│   ├── market_consumer.py    # CCXT WS consumer, normalizes feeds into GlobalState
│   ├── candle_buffer.py      # Aggregates ticks into OHLCV candles per timeframe
│   ├── decision_engine.py    # Orchestrates alpha→risk→execution pipeline per candle
│   ├── live_loop.py          # Main live trading async loop
│   └── shadow_loop.py        # Shadow mode async loop (same pipeline, no real orders)
├── commander/                # CLI command interface
│   └── main.py               # Typer CLI for bot management (start, status, backtest)
├── backtest/                 # Backtesting engine
│   ├── engine.py             # Core backtest runner: replays candles through pipeline
│   ├── orchestrator.py       # Multi-symbol/strategy backtest coordinator
│   ├── worker.py             # Worker process for parallel backtest execution
│   └── formatter.py          # Results formatting (tables, equity curves)
├── analytics/                # Performance analytics
│   ├── performance.py        # Sharpe, Sortino, max drawdown, win rate calculations
│   └── reconciliation.py     # Trade reconciliation (expected vs actual fills)
├── data_engine/              # Standalone data ingestion service
│   ├── main.py               # Entry point for data-engine container
│   ├── exchange_connector.py # CCXT exchange connection manager
│   ├── postgres_cacher.py    # Persists OHLCV candles to Postgres
│   └── redis_publisher.py    # Publishes live ticks to Redis streams
├── watchdog/                 # Key 6
│   ├── monitor.py              # Heartbeat monitor, latency tracker, event loop lag
│   ├── dead_mans_switch.py     # External health ping
│   └── system_watchdog.py      # System-level health checks (disk, memory, CPU)
└── bot/                      # Key 7 — Telegram Command Interface
    ├── handlers.py           # All command & callback handlers
    ├── runner.py             # PTB app builder, bot_data wiring, startup
    ├── alert_service.py      # Telegram alert sender
    └── utils/
        ├── format.py
        ├── telegram_helpers.py
        └── formatters/
            └── trade_history_formatter.py
tests/                        # see TESTING_STRATEGY.md for full layout
docs/
├── architecture/
│   └── adaptive_multi_strategy.md  # [NEW] Hub-and-Spoke design spec
├── execution/
│   └── active_position_manager.md  # [NEW] APM loop spec
└── risk/
    └── portfolio_risk_manager.md   # [NEW] Portfolio risk spec
```

---

## 4. Before Writing Any Code

1. Identify which of the 7 Keys (or `core/`) the change touches — read that component's section in `docs/ARCHITECTURE.md` and its checklist in `docs/DEFINITION_OF_DONE.md` §3.
2. If the change touches a Pydantic model or DB table, cross-check `docs/DATA_MODEL.md` field-for-field. Don't extrapolate a shape.
3. If the change touches execution, risk gates, or the watchdog, re-read the relevant section of `docs/RISK_AND_RUNBOOK.md` — these are the parts of the system where a plausible-looking shortcut can lose real money.
4. Check `CONTEXT.md` §7 for whether this area of the system has an open doc conflict. If yes, do not silently resolve it — ask.
5. **Phase 6 additions:** If the change touches `RegimeClassifier`, `StrategyRouter`, `ActivePositionManager`, or `PortfolioRiskManager`, also read the corresponding spec doc in `docs/architecture/`, `docs/execution/`, or `docs/risk/` before writing a single line.
6. **Shadow Mode:** If the change touches `ShadowExecutor`, `ShadowAPM`, or shadow stores → read `docs/review/refinement_shadom_plan.md` for the 4 critical refinements (fee asymmetry, wick miss, funding drag, pending limits) before writing code.
7. **New modules:** If the change touches `app/consumer/`, `app/commander/`, `app/backtest/`, `app/analytics/`, or `app/data_engine/`, also read the corresponding agent section in §8 of this file before writing.

---

## 5. Testing Requirements

Full detail in `docs/TESTING_STRATEGY.md`. Minimum bar for any PR:

- New logic in `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, `app/data/filters.py`, `app/consumer/`, `app/backtest/`, or `app/data_engine/` → unit tests with >90% coverage, including the mandatory edge cases (divide-by-zero, missing exchange, bad tick, empty book).
- New DB writes → integration test asserting the row matches `docs/DATA_MODEL.md` schema exactly.
- Anything touching kill switch, circuit breakers, reconciliation, or proxy failover → a corresponding test from `docs/TESTING_STRATEGY.md` §5 must exist and pass. If no such test exists yet for the behavior you're adding, write it — don't skip it.
- **Phase 6 specifics:**
  - `RegimeClassifier` → unit tests for all 4 regime outputs + boundary conditions (ADX=25.0, Hurst=0.45, ATR percentile=80).
  - `StrategyRouter` → unit tests for all 3 scoring branches; mock `global_data` to verify fakeout detection.
  - `ActivePositionManager` → integration tests verifying `amend_stop_loss()` is called on the +1R trigger and on regime shift.
  - `PortfolioRiskManager` → unit tests for correlation cap (exact cap-3 scenario), gross/net exposure math, circuit breaker state propagation.
  - **Shadow system (zero tests exist today — flag gap):**
    - `ShadowExecutor` → unit tests for fee asymmetry (maker vs taker routing), slippage calculation, pending limit order state machine, order ID generation
    - `ShadowAPM` → unit tests for worst_price_seen wick detection, funding rate deduction timing (8h boundary), PENDING→OPEN state transition, TTL expiry
    - `ShadowPositionStore` / `ShadowTradeStore` → integration test asserting `shadow_trades` table round-trip matches `docs/DATA_MODEL.md`
    - Integration: shadow mode skips reconciliation + position_reconciler when `SHADOW_MODE_ENABLED=true`; shadow and live Redis keys never collide
- Run locally before claiming done: `pytest`, `ruff check .`, `black --check .`, `mypy --strict app/`.

---

## 6. Do NOT

- Do not add Grafana, a microservice split, or an LLM in the hot execution path (SOR/risk gate). **AI is mandatory in two safe positions** (pre-entry CryptoAnalyst, post-entry PositionJudge) via 9router proxy — do not make it optional or skippable. See `docs/review/ai_layer_analysis.md` for the latency math.
- Do not weaken or bypass the Kill Switch, Circuit Breakers, or Startup Reconciliation for convenience during development (e.g., "just comment this out for local testing"). If it needs a dev-mode bypass, that bypass must be explicit, logged, and never the default.
- Do not invent a Prometheus metric name, Postgres column, or Pydantic field that isn't in `docs/DATA_MODEL.md` or `docs/DEFINITION_OF_DONE.md` §4 without flagging it as a new addition for review.
- Do not mark something "done" without walking the actual `docs/DEFINITION_OF_DONE.md` checklist for that component — not just "tests pass."
- **Do not bypass `PortfolioRiskManager`.** It is not optional scaffolding — it is a mandatory pre-trade gate. Calling `BybitExecutor` without first passing through `PortfolioRiskManager` is a hard reject.
- **Do not soft-code the Regime Shift Kill Switch.** This is not a configurable toggle. The APM must always check for regime changes and market-close the position on a shift. No exceptions.
- **Do not implement numeric thresholds from CONTEXT.md §7 Issue #10 or #11 until team ratification.** Use placeholder constants from `SYSTEM_CONSTANTS.md` and leave a TODO comment.

---

## 7. Conflict Resolution Protocol

If you (Codex) find a requirement that contradicts another doc — including the ones already logged in `CONTEXT.md` §7 — do not guess which one wins based on which seems more recent or more detailed. State the conflict plainly, cite both sources, and ask. This has already happened in this doc set (drawdown threshold, Redis scope, consecutive loss counts) — it will happen again as the docs evolve, and picking silently is how a numeric typo becomes a real drawdown incident.

---

## 8. Module Personas (Logical Agents)

The system's logical responsibilities are divided across three "agent" personas. These are not separate processes — everything runs in the single `asyncio` process — but thinking of them as agents clarifies ownership and responsibility.

### 🔬 Alpha Agent (Key 2 — `app/alpha/`)

**Mission:** Know the market's current personality and generate calibrated confidence scores for entries.

**Owns:**
- `RegimeClassifier` (The Hub): Continuously classifies market state into TREND_BULL, TREND_BEAR, RANGE, or CHOP using ADX + Hurst Exponent + ATR percentile. The single source of truth for what the market is doing right now.
- `StrategyRouter` (The Spokes): Applies regime-specific scoring rules to candidate signals:
  - **Trend:** Momentum/breakout confirmation + global exchange sync (fakeout detection)
  - **Range:** Bollinger Band edge-fade + wick rejection + RSI exhaustion
  - **Chop:** Orderbook liquidity sweep + funding rate extremes
- `signals.py`: Multi-signal composite (skew + lead_lag + funding + OI)
- `regime.py`: Existing Hurst + ADX classifier (to be refactored into `regime_classifier.py`)
- `entry_filter.py`: Pre-entry structural checklist
- `analyst.py`: MANDATORY AI pre-entry review via 9router
- `position_judge.py`: MANDATORY AI post-entry position assessment
- `multi_tf.py`: 4H trend confirmation gate
- `trade_memory.py`: Historical trade context injection for AI prompts

**Invariants the Alpha Agent must never violate:**
- `RegimeClassifier` output is the single source of truth for regime. No module may infer regime from raw price data independently.
- A CHOP regime means `confidence = 0.0`. No signal is passed forward.
- AI calls must always happen after deterministic signal generation, never before.
- If the 9router AI call fails → signal is REJECTED (not bypassed, not degraded to deterministic-only).

---

### 🛡️ Risk Agent (Key 3 — `app/risk/`)

**Mission:** Prevent any single trade or sequence of trades from causing unrecoverable account damage.

**Owns:**
- `PortfolioRiskManager` (Pre-Trade Gate, NEW): The first filter after a signal is generated. Checks:
  1. Correlation trap: max 2 concurrent positions in any correlated sector (altcoins, L1s, etc.)
  2. Gross exposure limit: total notional (all open positions) ≤ configured % of equity
  3. Net exposure limit: directional imbalance (longs − shorts) ≤ configured % of equity
  4. CircuitBreaker state: if CB has fired today, block all new entries
- `CircuitBreaker` (Systemic, NEW): Portfolio-level daily loss limit and consecutive loss counter. **Separate from the existing per-session -2% hard stop.**
- `gates.py` (existing): 3-layer gate — liquidity, spread health, per-session circuit breaker
- `circuit_breaker.py` (existing): Per-session -2% drawdown hard stop
- `sector_cap.py` (existing): Max 2 positions per sector

**Execution order (mandatory):**

```text
PortfolioRiskManager.check()     ← NEW, runs first
    ↓ passes
gates.check()                    ← existing 3-layer gate
    ↓ passes
sector_cap.check()               ← existing
    ↓ passes
BybitExecutor.execute()          ← never call without passing all above
```

**Invariants the Risk Agent must never violate:**
- `PortfolioRiskManager` runs before `BybitExecutor`. No exceptions.
- The existing -2% hard stop in `circuit_breaker.py` cannot be weakened or bypassed. The new portfolio-level CircuitBreaker is additive — not a replacement.
- All thresholds for the new CircuitBreaker (daily loss %, consecutive loss count) come from `SYSTEM_CONSTANTS.md`. Never hardcode them inline.
- If `PortfolioRiskManager` state is unavailable (Redis down, etc.), the default behavior is BLOCK (fail-safe, not fail-open).

---

### ⚙️ Execution / Management Agent (Key 4 — `app/execution/`)

**Mission:** Fill orders with optimal market impact and defend every open position actively until closure.

**Owns:**
- `bybit_client.py` (existing): Bybit REST/WS client; places and amends exchange-side SL/TP
- `sor.py` (existing): Smart Order Routing — Post-Only → Reprice → Market/IOC
- `position_lifecycle.py` (existing): TrailingStopManager + CheckpointManager (AI-escalated exits)
- `ActivePositionManager` (APM, NEW): The continuous 2-second async monitoring loop that runs for every open position after fill:
  - Calculates live R-Multiple every cycle
  - Enforces the **+1R Breakeven Lock**: at +1R, amends exchange-side SL to entry + fees
  - Enforces **Regime-Specific Trailing Stop**: Chandelier ATR trailing for TREND, time-exits for RANGE/CHOP
  - Enforces the **Regime Shift Kill Switch**: if market regime changes, closes position at market immediately
  - Runs **Ghost Position Reconciliation** every 5 minutes: compares internal state vs Bybit REST API

**Invariants the Execution Agent must never violate:**
- Every SL/TP amendment MUST call `bybit_client.amend_stop_loss()` / `amend_take_profit()` — no in-memory-only SL tracking.
- The +1R Breakeven Lock triggers exactly once per position (guarded by `moved_to_breakeven` flag in state). Never re-trigger.
- The Regime Shift Kill Switch cannot be disabled. It is not a configurable toggle.
- APM loops must have `try/except Exception` + `await asyncio.sleep(5)` on the error path. A crashing APM loop must not take down the entire event loop.
- On any `amend_stop_loss()` failure, log CRITICAL and alert Telegram. Do not silently swallow the error.
- `initial_risk_per_unit` must be set at trade entry using `Decimal` and stored in Postgres. The APM reads it from DB — it does not recalculate it.

---

### 👻 Shadow Execution Agent (`app/execution/shadow.py` + `app/core/shadow_store.py`)

**Mission:** Simulate the full trade lifecycle on live market data without placing real orders. Validate strategy math, fee impact, and slippage assumptions before going live.

**Owns:**
- `ShadowExecutor`: Simulated order routing. Same `execute()`/`execute_exit()` interface as SmartOrderRouter. Applies asymmetric fees (maker vs taker based on `is_post_only`) and simulated slippage.
- `ShadowAPM`: Virtual position management. Monitors live Redis prices. Detects SL hits via `worst_price_seen` (wick miss prevention). Deducts 8-hour funding rate drag. Processes `PENDING_VIRTUAL_FILL` limit orders.
- `ShadowExchangeClient`: Redis-backed mock BybitClient for APM compatibility.
- `ShadowPositionStore`: Redis position state with `shadow:position:*` key prefix.
- `ShadowTradeStore`: Postgres CRUD targeting `shadow_trades` table.

**State Isolation Rules:**
- Shadow positions use `shadow:position:{symbol}:{side}` Redis keys. NEVER `position:{symbol}:{side}`.
- Shadow trades write to `shadow_trades` table. NEVER `trades`.
- Startup reconciliation is explicitly skipped when `SHADOW_MODE_ENABLED=true`.
- Position reconciler task is not started when `SHADOW_MODE_ENABLED=true`.

**Invariants the Shadow Agent must never violate:**
- Fee type must match order type: `is_post_only=True` → `shadow_maker_fee_pct`; `is_post_only=False` → `shadow_taker_fee_pct`.
- `worst_price_seen` must be updated every 2s cycle and persisted to Redis. SL detection uses worst price, not current price.
- Funding fee deduction triggers at 8-hour intervals using live funding rate from Redis.
- Pending limit orders expire after `SHADOW_PENDING_TTL_SECS` (600s) if price doesn't cross virtual entry.
- Shadow mode activation is via `SHADOW_MODE_ENABLED=true` env var — not a runtime toggle.

### 📡 Market Consumer Agent (`app/consumer/`)

**Mission:** Ingest market data from exchanges, buffer into candles, and drive the live/shadow trading loops.

**Owns:**
- `MarketConsumer`: CCXT WebSocket consumer that normalizes raw exchange data into `GlobalState`.
- `CandleBuffer`: Aggregates raw ticks into OHLCV candles per timeframe (1m, 5m, 15m, 1h, 4h).
- `DecisionEngine`: Orchestrates the alpha→risk→execution pipeline on each candle close.
- `LiveLoop`: Main live trading async loop. Runs DecisionEngine on real market data.
- `ShadowLoop`: Shadow mode async loop. Same pipeline as LiveLoop but routes through ShadowExecutor/SOR.

**Invariants:**
- Both `LiveLoop` and `ShadowLoop` must include `try/except` + `await asyncio.sleep()` on error paths (same crash-safety rule as APM).
- `CandleBuffer` must validate candle completeness before emitting close events.
- `MarketConsumer` must handle WS reconnection gracefully — never silently drop a feed.

### ⌨️ Commander Agent (`app/commander/`)

**Mission:** CLI interface for bot management — start, status, backtest launch.

**Owns:**
- `main.py`: Typer CLI entrypoint. Commands for starting the bot, checking status, launching backtests.

**Invariants:**
- Commander must not bypass any safety gate (risk check, reconciliation) when starting the bot.
- All CLI commands must validate environment/config before executing.

### 🔬 Backtest Agent (`app/backtest/`)

**Mission:** Replay historical candles through the full pipeline to validate strategies before live deployment.

**Owns:**
- `engine.py`: Core backtest runner. Replays candles through alpha→risk→execution pipeline.
- `orchestrator.py`: Multi-symbol/strategy coordinator for batch backtesting.
- `worker.py`: Worker process for parallel backtest execution.
- `formatter.py`: Results formatting — tables, equity curves, trade summaries.

**Invariants:**
- Backtest engine must use identical pipeline code as live — no simplified "backtest-only" shortcuts.
- Results must be stored in Postgres (`backtest_results` table) per `docs/DATA_MODEL.md`.
- Fee/slippage model must match ShadowExecutor's fee asymmetry (maker vs taker).

### 📊 Analytics Agent (`app/analytics/`)

**Mission:** Compute performance metrics and reconcile trades.

**Owns:**
- `performance.py`: Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor.
- `reconciliation.py`: Compares expected fills (from DecisionEngine) vs actual fills (from Bybit).

**Invariants:**
- All PnL calculations must use `decimal.Decimal` — never `float`.
- Reconciliation must flag discrepancies for operator review via Telegram alert.

### 🗄️ Data Engine Agent (`app/data_engine/`)

**Mission:** Standalone data ingestion service. Runs as a separate Docker container (routed through gluetun VPN).

**Owns:**
- `main.py`: Entry point for data-engine container.
- `exchange_connector.py`: CCXT exchange connection manager with VPN-aware config.
- `postgres_cacher.py`: Persists OHLCV candles to Postgres for backtest use.
- `redis_publisher.py`: Publishes live ticks to Redis streams for consumer pickup.

**Invariants:**
- Data engine must be routed through gluetun VPN sidecar for Bybit API access.
- Published Redis stream keys must match what `MarketConsumer` expects.
- Postgres candle schema must match `docs/DATA_MODEL.md` (`historical_candles` table).