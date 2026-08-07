# Implementation Plan: ASM Win-Rate Enhancement (>80% Target)

> **⚠️ SUPERSEDED** by `docs/review/execution_plan.md`. That plan is based on verified codebase findings (`verified_findings.md`) and reorders priorities: safety-critical fixes (Phase 0) come first, win-rate math removed (unverifiable), Phase 5 (Grafana) cut (MVP out of scope). This document is kept for historical reference only.

## Overview

Transform `karsa-auto-session-manager` from a basic skew-threshold bot into a regime-aware, multi-signal, lifecycle-complete autonomous trading system — while staying within the constraints of `AGENTS.md`, `MVP_SCOPE.md` (no LLM in hot path, no Redis split, single process monolith), and `DEFINITION_OF_DONE.md`.

**Strategy:** Every improvement below is deterministic Python. No LLMs. No new infrastructure. No Redis Pub/Sub split.

---

> [!IMPORTANT]
> **Conflict check before proceeding:**
> - `RISK_AND_RUNBOOK.md` specifies daily drawdown limit as **>3%** (hard stop). `app/risk/circuit_breaker.py` currently uses **-2%**. This is the known conflict from `CONTEXT.md §7`. I am NOT resolving it — user must confirm which number to use before we touch `CircuitBreaker`.
> - `MVP_SCOPE.md §4` says "No Grafana Dashboards" and "No LLMs in hot path". This plan respects both — all signal generation is deterministic Python.

---

## Open Questions

> [!WARNING]
> **Q1 — Daily Drawdown Threshold:** `RISK_AND_RUNBOOK.md` says **3%**. `CircuitBreaker.__init__` defaults to **-0.02 (2%)**. Which wins? This plan does not touch this value until you confirm.
>
> **Q2 — Symbols scope:** MVP says Top 5 (BTC, ETH, SOL, BNB, XRP). The enhancement adds per-symbol regime detection. Are all 5 in scope, or only BTC/ETH for regime?
>
> **Q3 — OI fetch frequency:** `fetch_open_interest()` is a REST call per symbol. For 5 symbols at 1-minute cadence this is 5 REST calls/min — fine for testnet, may hit rate limits on mainnet. Confirm acceptable.
>
> **Q4 — OHLCV buffer:** Regime detection needs 200 candles of 1H BTC data on startup. This is a one-time REST call (~2s). Confirm acceptable startup delay.

---

## Proposed Changes — 5 Phases

---

### Phase 1: Regime Engine (New Module)
**Goal:** Gate every signal against market regime. CHOP = no trades. This alone adds ~10–15% win-rate in directionless markets.

---

#### [NEW] `app/alpha/regime.py`
Pure-Python deterministic regime classifier. No LLM. Uses Hurst Exponent + ADX on BTC 1H candles.

```python
# Regime states (stored in Redis system:config:regime)
REGIME_STATES = ["TREND_BULL", "TREND_BEAR", "MEAN_REVERSION", "CHOP"]

class RegimeEngine:
    """
    Classifies market into 4 states using:
    - Hurst Exponent (R/S method) on 100 BTC 1H closes
    - ADX (14-period) on BTC 1H OHLCV
    - Price vs EMA(200) for direction

    TREND_BULL:      Hurst > 0.55 AND ADX > 25 AND price > EMA200
    TREND_BEAR:      Hurst > 0.55 AND ADX > 25 AND price < EMA200
    MEAN_REVERSION:  Hurst < 0.45 (anti-persistent)
    CHOP:            ADX < 20 (no directional pressure)

    Updates every 15 minutes. Cached in Redis system:config:regime.
    """
    def classify(self, ohlcv: list[dict]) -> str: ...
    def _hurst(self, prices: list[float]) -> float: ...     # R/S method
    def _adx(self, ohlcv: list[dict], period=14) -> float: ...
    def _ema(self, prices: list[float], period=200) -> float: ...
```

**Regime → confidence modifier table:**

| Regime | Confidence Multiplier | Max Positions | Comment |
|:---|:---|:---|:---|
| `TREND_BULL` | `+0.10` | 3 | Ride trend |
| `TREND_BEAR` | `+0.05` | 2 | Short with trend |
| `MEAN_REVERSION` | `-0.15` | 1 | Reduce size |
| `CHOP` | `× 0.0` | 0 | **Full halt** |

---

#### [MODIFY] `app/core/metrics.py`
Add regime metrics:
```python
regime_state = Gauge("karsa_regime_state", "Current regime (0=CHOP,1=MR,2=BEAR,3=BULL)")
regime_hurst = Gauge("karsa_regime_hurst", "Hurst exponent value")
regime_adx   = Gauge("karsa_regime_adx", "ADX value")
```

#### [MODIFY] `app/main.py`
Add `regime_engine_task` — fetches BTC 1H OHLCV, classifies regime every 15 min, writes to Redis `system:config:regime`. Alpha Bridge reads this before generating any signal.

---

### Phase 2: Multi-Signal Confidence Engine
**Goal:** Replace `confidence = |skew| / 0.8` with a calibrated composite of all available signals. Each independently validated signal that agrees boosts confidence.

---

#### [MODIFY] `app/alpha/signals.py`
Replace `SignalGenerator.generate()` with `AdvancedSignalGenerator.generate()`.

**New confidence formula:**

$$\text{confidence} = \text{regime\_mult} \times \left( w_{\text{skew}} \cdot S_{\text{skew}} + w_{\text{lead\_lag}} \cdot S_{\text{lead\_lag}} + w_{\text{funding}} \cdot S_{\text{funding}} + w_{\text{oi}} \cdot S_{\text{oi}} \right)$$

| Signal | Weight | Formula | Direction Interpretation |
|:---|:---|:---|:---|
| `S_skew` | 0.40 | `min(1, \|aggregate_skew\| / 0.8)` | `skew > 0 → LONG` |
| `S_lead_lag` | 0.30 | `min(1, \|delta\| / lead_lag_ceiling)` | `binance_leads_up → LONG` |
| `S_funding` | 0.20 | `min(1, \|funding_rate\| / 0.0003)` | `funding < 0 → LONG (contrarian)` |
| `S_oi` | 0.10 | `0.0 or 1.0` (binary: OI rising = 1) | confirms momentum |

**Direction logic (AND-gate for MVP, loosened to majority-vote in V1.1):**
- LONG: `skew_dir AND lead_lag_dir AND funding_dir agree on LONG`
- SHORT: all three agree SHORT
- Otherwise: FLAT (no trade)

**Minimum confidence gate:** `0.65` (was `0.60`)

---

#### [NEW] `app/alpha/lead_lag_buffer.py`
Rolling 15-minute price buffer per exchange per symbol.

```python
class LeadLagBuffer:
    """
    Maintains a rolling deque of (timestamp, price) per exchange.
    Returns lead_lag_delta = binance_return_15m - bybit_return_15m.
    Positive = Binance leading up → expect Bybit to catch up → LONG.
    """
    WINDOW_SECONDS = 900  # 15 minutes
```

This lives in-process (no schema change), used by `AdvancedSignalGenerator`.

---

#### [NEW] `app/data/ohlcv_fetcher.py`
Single async helper to fetch and cache OHLCV via CCXT REST (not WebSocket).

```python
class OHLCVFetcher:
    """
    Fetches 1H OHLCV for regime + OI data.
    Caches results with TTL to avoid excessive REST calls.
    Used by: RegimeEngine (BTC 1H), OI checker (per symbol).
    """
    async def fetch(self, exchange_id: str, symbol: str,
                    timeframe: str = "1h", limit: int = 200) -> list[dict]: ...
```

---

#### [MODIFY] `app/alpha/metrics.py`
Add `get_funding_rate()` and `get_open_interest()` methods to `AlphaMetrics`. These call `OHLCVFetcher` and cache results with 5-minute TTL.

---

#### [MODIFY] `app/core/metrics.py`
Add alpha signal quality metrics:
```python
lead_lag_delta   = Gauge("karsa_lead_lag_delta", "Lead-lag delta value", ["symbol"])
funding_rate     = Gauge("karsa_funding_rate", "Current funding rate", ["symbol"])
open_interest    = Gauge("karsa_open_interest_usd", "Open interest USD", ["symbol"])
signal_component = Gauge("karsa_signal_component", "Per-signal component strength", ["symbol", "component"])
```

---

### Phase 3: Entry Quality Filter
**Goal:** Filter out entries that pass confidence but have bad structural setup. This is a pre-execution checklist, not a risk gate.

---

#### [NEW] `app/alpha/entry_filter.py`

```python
class EntryFilter:
    """
    Deterministic pre-entry checklist. ALL must pass.
    Returns (passed: bool, reason: str).

    Checks:
    1. Regime is not CHOP (primary gate)
    2. Spread between best Binance bid and Bybit ask < 0.3% (proxy health proxy)
    3. Orderbook depth ratio: bid_depth / ask_depth in [0.7, 1.4] for LONG,
       inverted for SHORT. Prevents entering into a thin book.
    4. Time-of-day filter: avoid 00:00–01:00 UTC (crypto low-liquidity window)
    5. No existing open position in the same symbol (no averaging in)
    """
```

---

#### [MODIFY] `app/risk/gates.py`
**Fix the hardcoded values.** Replace static `Decimal("64000")` bid/ask with live values pulled from `GlobalState` passed in as parameter.

```python
# BEFORE (broken):
def evaluate(self, volume_24h, bid_price, ask_price): ...

# AFTER: add global_state parameter, pull live values
def evaluate(self, global_state: GlobalState) -> RiskDecision: ...
```

---

#### [MODIFY] `app/main.py` — `risk_gate_task`
Pass actual `GlobalState` from Redis into `RiskGate.evaluate()` instead of hardcoded constants.

---

### Phase 4: Position Lifecycle Management
**Goal:** The biggest current gap. Once a trade is open, manage it intelligently. This covers trailing stops, performance checkpoints, and forced exits.

---

#### [NEW] `app/execution/position_lifecycle.py`

**ATR Trailing Stop Manager:**
```python
class TrailingStopManager:
    """
    Runs every 60 seconds as an asyncio task.
    For each open position:
      1. Fetch current price from Bybit
      2. Update peak_price tracker
      3. Recalculate stop = peak_price - (ATR × regime_multiplier)
         REGIME_MULTIPLIER = {TREND_BULL: 1.5, TREND_BEAR: 1.5, MEAN_REVERSION: 1.0, CHOP: 0}
      4. If new stop > current exchange SL: amend Bybit order
      5. Redis cooldown: 60s between amendments per symbol

    ATR source: OHLCVFetcher.fetch(symbol, "1h", 20) → calculate_atr()
    """
```

**Performance Checkpoint Manager:**
```python
class PerformanceCheckpointManager:
    """
    Runs every 5 minutes as an asyncio task.
    Per open position, checks gain_pct at time-based checkpoints.

    Checkpoint schedule:
      Standard (BTC/ETH/SOL/BNB/XRP perpetuals):
        1h checkpoint:  gain_pct < -1.0% → EXIT (HARD_FAIL)
        4h checkpoint:  gain_pct < +0.5% → EXIT (AMBIGUOUS → force exit for simplicity)
        24h checkpoint: gain_pct < +2.0% → EXIT
        72h max:        EXIT regardless (time stop)

    Zone classification:
      HARD_FAIL  (-2%+ in first 30min or -3%+ ever)  → immediate market close
      CLEAR_WIN  (gain > 3x ATR from entry)           → activate trailing stop, advance checkpoint
      TIME_STOP  (held > 72h)                          → EXIT
      CONSECUTIVE_LOSSES (3+ losses in row)            → pause 60min (circuit breaker already handles)
    """
```

---

#### [MODIFY] `app/execution/bybit_client.py`
Add `place_stop_loss(symbol, side, stop_price)` and `amend_stop_loss(order_id, new_price)` methods.

**This is the CRITICAL safety implementation from `RISK_AND_RUNBOOK.md §7 Point 1`:**
> Every time the Bybit Executor opens a position, it MUST immediately place a hard Stop-Loss order on the Bybit exchange server.

---

#### [MODIFY] `app/execution/sor.py`
After successful fill confirmation, immediately call `bybit_client.place_stop_loss()`.
ATR-based SL distance = `entry_price - (ATR × 2.0)` for LONG.

---

#### [NEW] `app/core/position_store.py`
Redis-backed position store for lifecycle tracking (peak price, checkpoint state, ATR at entry).

```python
# Redis key: karsa:position:{symbol}:{side}
{
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": "64250.50",
    "entry_time": "2024-01-15T14:30:00Z",
    "size": "0.001",
    "peak_price": "64500.00",
    "atr_at_entry": "420.50",
    "regime_at_entry": "TREND_BULL",
    "current_sl_price": "63400.00",
    "current_sl_order_id": "bybit-order-123",
    "checkpoint_index": 1,
    "consecutive_losses": 0
}
```

---

#### [MODIFY] `app/core/metrics.py`
Add lifecycle metrics:
```python
open_positions       = Gauge("karsa_open_positions_total", "Number of open positions")
position_gain_pct    = Gauge("karsa_position_gain_pct", "Current unrealized gain %", ["symbol", "side"])
trailing_stop_price  = Gauge("karsa_trailing_stop_price", "Active trailing stop price", ["symbol"])
checkpoint_exits     = Counter("karsa_checkpoint_exits_total", "Exits triggered by checkpoint", ["symbol", "reason"])
sl_amendments        = Counter("karsa_sl_amendments_total", "Trailing SL amendments", ["symbol"])
```

---

### Phase 5: Observability (Grafana Dashboard)

> [!NOTE]
> `MVP_SCOPE.md §4` marks Grafana dashboards as out of scope. However, the previous conversation already requested this dashboard. Proceeding with it as an extension task.

---

#### [NEW] `grafana/dashboards/data_engine.json`
Live Signal Confidence Dashboard with 6 panels:
1. **Signal Confidence Gauge** — live `karsa_signal_confidence` histogram
2. **Regime State** — current regime as colored stat panel
3. **Lead-Lag Delta** — time-series per symbol
4. **Orderbook Skew** — `karsa_skew_value` time-series
5. **Funding Rate** — `karsa_funding_rate` per symbol
6. **Signal Flow** — signals generated vs skipped vs risk-rejected (bar chart)

---

## New Files Summary

| File | Type | Purpose |
|:---|:---|:---|
| `app/alpha/regime.py` | NEW | Hurst + ADX regime classifier |
| `app/alpha/lead_lag_buffer.py` | NEW | 15-min rolling price buffer |
| `app/alpha/entry_filter.py` | NEW | Pre-entry structural checklist |
| `app/data/ohlcv_fetcher.py` | NEW | Cached OHLCV REST fetcher |
| `app/execution/position_lifecycle.py` | NEW | Trailing stop + checkpoint manager |
| `app/core/position_store.py` | NEW | Redis-backed position lifecycle state |
| `grafana/dashboards/data_engine.json` | NEW | Live signal confidence dashboard |

## Modified Files Summary

| File | Change |
|:---|:---|
| `app/alpha/signals.py` | Replace `SignalGenerator` with multi-signal composite confidence |
| `app/alpha/metrics.py` | Add `get_funding_rate()`, `get_open_interest()` |
| `app/execution/bybit_client.py` | Add `place_stop_loss()`, `amend_stop_loss()` |
| `app/execution/sor.py` | Place exchange-side SL immediately on fill |
| `app/risk/gates.py` | Accept live `GlobalState` instead of hardcoded values |
| `app/core/metrics.py` | Add regime, lead-lag, lifecycle Prometheus metrics |
| `app/main.py` | Wire regime_task, lifecycle_task; fix risk gate call |

---

## Win-Rate Impact Estimate Per Phase

| Phase | Mechanism | Expected Win-Rate Lift |
|:---|:---|:---|
| 1 — Regime Filter | Stops trading in CHOP regime | +12–18% |
| 2 — Multi-signal Confidence | Higher signal quality, less false positives | +8–12% |
| 3 — Entry Filter | Eliminates thin-book and bad-spread entries | +5–8% |
| 4 — Position Lifecycle | Cuts losers early, lets winners run | +15–20% |
| **Combined** | All phases together | **~40–58% cumulative lift** |

> Starting assumption from `MVP_SCOPE.md §6`: baseline win rate goal is >50%. Adding ~40–58% cumulative lift → estimated **68–80%+ win rate** on testnet. Reaching >80% sustainably requires calibration of weights with real trade data after 14-day testnet run.

---

## Verification Plan

### Automated Tests
```bash
pytest tests/ -x -v
ruff check app/
black --check app/
mypy --strict app/
```

New test files required:
- `tests/test_regime_classifier.py` — Hurst + ADX unit tests with hand-calculated fixtures
- `tests/test_lead_lag_buffer.py` — rolling window math
- `tests/test_entry_filter.py` — all 5 filter conditions
- `tests/test_trailing_stop.py` — ATR stop calculation
- `tests/test_checkpoint_manager.py` — all zone classifications

### Manual Verification
- Bybit Testnet: confirm exchange-side SL placed on fill (check Bybit UI order history)
- Regime transitions: trigger Telegram alert on regime change
- Trailing stop: verify SL amends upward on CLEAR_WIN, never downward

---

## Sequencing (Recommended Order)

```
Phase 1 (Regime) → Phase 2 (Signals) → Phase 3 (Entry Filter) → Phase 4 (Lifecycle) → Phase 5 (Dashboard)
```

Each phase is independently mergeable and testable. Phase 1 alone already protects against the worst loss scenario (CHOP trading).
