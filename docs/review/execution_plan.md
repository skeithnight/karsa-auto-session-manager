# Execution Plan: ASM Win-Rate Enhancement

> Based on verified findings from `verified_findings.md`. Reordered by actual priority, not plan's original order.

---

## Blockers (Must Resolve Before Starting)

### B1: Drawdown Threshold Conflict
- `circuit_breaker.py:18` = **2%** (code)
- `RISK_AND_RUNBOOK.md:34` = **3%** (docs)
- **Action:** User picks one. Code changes to match.
- **BLOCKS:** Nothing — but must be decided before touching circuit breaker.

### B2: Redis Scope
- Code already uses Redis for 7+ keys. Docs inconsistent.
- Phase 4 adds `position_store.py` (Redis-backed).
- **Action:** User confirms Redis is in scope. Docs updated to match reality.
- **BLOCKS:** Phase 4 (`position_store.py`).

---

## Phase 0: Safety-Critical Fixes (P0 — Do First)

### 0A: Wire Real Data into RiskGate
**File:** `app/main.py:135-139`
- Replace hardcoded `Decimal("5000000")`, `Decimal("64000")`, `Decimal("64100")`
- Pull live values from `StateManager.get_global_state(symbol)` → Redis `global:state:{symbol}`
- Extract `bid_price`, `ask_price`, `volume_24h` from the state dict
- **Also fix:** `gates.py:18` — change `daily_drawdown_limit` from `float` to `Decimal` (CLAUDE.md rule)

**Effort:** ~30 min. Single call site fix + type fix.

### 0B: Exchange-Side Stop Loss on Fill
**Files:** `app/execution/bybit_client.py`, `app/execution/sor.py`

1. Add to `BybitClient`:
   - `place_stop_loss(symbol, side, stop_price) -> dict` — places conditional close order via Bybit API
   - `amend_stop_loss(order_id, new_price) -> dict` — amends existing SL order
2. Modify `SmartOrderRouter.execute()`:
   - After successful fill, calculate SL distance: `ATR × 2.0` (use a default initial value; ATR wiring comes in Phase 3)
   - Call `bybit_client.place_stop_loss()` immediately
   - Store `sl_order_id` in returned order dict
3. Add tests: mock BybitClient, verify SL placed on every fill path

**Effort:** ~2-3 hours. Two new methods + sor.py wiring + tests.

**This is the #1 safety gap.** Every position without an exchange-side SL is unprotected.

---

## Phase 1: Regime Engine (New Module)

### 1A: OHLCV Fetcher
**New file:** `app/data/ohlcv_fetcher.py`
- `OHLCVFetcher` class using ccxt REST (not WebSocket)
- `async fetch(exchange_id, symbol, timeframe, limit) -> list[dict]`
- In-memory TTL cache (5 min for 1H candles, 1 min for 15m)
- Used by RegimeEngine and later by lead-lag/ATR

**Effort:** ~1 hour.

### 1B: Regime Classifier
**New file:** `app/alpha/regime.py`
- `RegimeEngine` class: Hurst Exponent (R/S) + ADX(14) + EMA(200) on BTC 1H
- `classify(ohlcv) -> str` returns one of: `TREND_BULL`, `TREND_BEAR`, `MEAN_REVERSION`, `CHOP`
- Classification thresholds from implementation plan (Hurst > 0.55, ADX > 25, etc.)
- Updates every 15 min, writes to Redis `system:config:regime`

### 1C: Wire Regime into Signal Generation
**Files:** `app/alpha/signals.py`, `app/main.py`
- `SignalGenerator.generate()` reads regime from Redis before generating signal
- CHOP → force FLAT, skip signal
- Other regimes → apply confidence modifier

### 1D: Tests
- `tests/test_regime_classifier.py` — Hurst + ADX unit tests with hand-calculated fixtures
- `tests/test_ohlcv_fetcher.py` — cache TTL, error handling

**Phase 1 effort:** ~4-5 hours total.

---

## Phase 2: Multi-Signal Confidence

### 2A: Lead-Lag Buffer
**New file:** `app/alpha/lead_lag_buffer.py`
- `LeadLagBuffer` class: rolling 15-min deque per exchange per symbol
- `get_lead_lag_delta(symbol) -> float` — Binance 15m return minus Bybit 15m return
- In-process (no schema change)

### 2B: Funding Rate Integration
**Modify:** `app/alpha/metrics.py`
- Add `get_funding_rate(symbol) -> Optional[Decimal]` — fetch from Bybit via ccxt
- Cache with 5-min TTL
- Contrarian: `funding < -0.0003` → bias LONG

### 2C: Open Interest Integration
**Modify:** `app/alpha/metrics.py`
- Add `get_open_interest(symbol) -> Optional[Decimal]` — fetch from Bybit via ccxt
- Cache with 5-min TTL
- Binary: OI rising → 1.0, otherwise → 0.0

### 2D: Replace Confidence Formula
**Modify:** `app/alpha/signals.py`
- Replace `SignalGenerator` with composite confidence:
  ```
  confidence = regime_mult × (0.4 × S_skew + 0.3 × S_lead_lag + 0.2 × S_funding + 0.1 × S_oi)
  ```
- Direction: AND-gate (all 3 directional signals agree)
- Minimum confidence: `0.65`

### 2E: Tests
- `tests/test_lead_lag_buffer.py` — rolling window math
- `tests/test_signal_composite.py` — all signal combinations, edge cases

**Phase 2 effort:** ~5-6 hours total.

---

## Phase 3: Entry Quality Filter

### 3A: Pre-Entry Checklist
**New file:** `app/alpha/entry_filter.py`
- `EntryFilter.check(signal, global_state) -> (bool, str)`
- Checks: regime ≠ CHOP, spread < 0.3%, book depth ratio [0.7, 1.4], no 00:00–01:00 UTC, no existing position

### 3B: Wire into Pipeline
**Modify:** `app/main.py`
- Insert `EntryFilter` between signal generation and risk gate

### 3C: Tests
- `tests/test_entry_filter.py` — all 5 conditions, edge cases

**Phase 3 effort:** ~2-3 hours total.

---

## Phase 4: Position Lifecycle

> **BLOCKED on B2 (Redis scope confirmation).**

### 4A: Position Store
**New file:** `app/core/position_store.py`
- Redis-backed store for lifecycle tracking (peak price, checkpoint state, ATR, SL order ID)
- Key: `karsa:position:{symbol}:{side}`

### 4B: Trailing Stop Manager
**New file:** `app/execution/position_lifecycle.py` (part 1)
- Runs every 60s as asyncio task
- Per position: track peak, recalc stop = peak - (ATR × regime_multiplier)
- Amend Bybit SL if new stop > current stop
- 60s cooldown per symbol between amendments

### 4C: Performance Checkpoints
**New file:** `app/execution/position_lifecycle.py` (part 2)
- Runs every 5 min as asyncio task
- Checkpoint schedule: 1h / 4h / 24h / 72h time stop
- HARD_FAIL: -2%+ in first 30min or -3%+ ever → immediate exit
- CLEAR_WIN: gain > 3x ATR → activate trailing stop
- TIME_STOP: held > 72h → exit

### 4D: Wire into Main
**Modify:** `app/main.py`
- Add `lifecycle_task` to asyncio task list
- Phase 0B's SL placement feeds into position_store

### 4E: Tests
- `tests/test_trailing_stop.py` — ATR stop calculation
- `tests/test_checkpoint_manager.py` — all zone classifications

**Phase 4 effort:** ~6-8 hours total.

---

## Phase 5: Dashboard (Optional, MVP Scope Out)

> Skipped per MVP_SCOPE §4. Move to separate doc if needed.

---

## Execution Order

```
Resolve B1 (drawdown) + B2 (Redis)  ← user decision needed
    ↓
Phase 0A (wire risk gate)           ← 30 min, no blockers
Phase 0B (exchange-side SL)         ← 2-3h, no blockers
    ↓
Phase 1 (regime engine)             ← 4-5h, needs OHLCV fetcher
    ↓
Phase 2 (multi-signal)              ← 5-6h, needs lead-lag buffer
    ↓
Phase 3 (entry filter)              ← 2-3h, needs regime
    ↓
Phase 4 (position lifecycle)        ← 6-8h, needs Redis confirmed + SL in place
```

**Total estimated effort:** 20-26 hours of focused implementation.

---

## What This Plan Does NOT Cover (Deferred)

- AI PositionJudge (LLM in hot path — MVP out of scope)
- Sector diversity cap (P4-B from benchmark)
- Trade memory injection (P4-C from benchmark)
- Dynamic universe scoring (config has 35 symbols already; MVP says 5 — needs separate decision)
- Grafana dashboard (MVP out of scope)
