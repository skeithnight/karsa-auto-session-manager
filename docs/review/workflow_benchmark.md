# Workflow Benchmark: Win-Rate Analysis
## `karsa-auto-session-manager` (ASM) vs `karsa-claude-trading` (KCT)

> Goal: understand the trade lifecycle of each system, identify where win rate is being left on the table, and propose concrete improvements for ASM.

> **Status:** Superseded by `verified_findings.md` (code-verified audit) and `execution_plan.md` (reordered implementation plan). Original analysis was architectural gap analysis, not a benchmark — renamed in spirit. Win-rate math in §8 was unverifiable (no backtest baseline).

---

## 1. Side-by-Side Architecture Overview

| Dimension | ASM (`karsa-auto-session-manager`) | KCT (`karsa-claude-trading`) |
|:---|:---|:---|
| **Core paradigm** | Rule-based threshold engine | AI-augmented regime-adaptive engine |
| **Signal generation** | Deterministic: skew > 0.3 threshold | LLM agent (CryptoAnalyst) + deterministic TA |
| **Regime awareness** | ❌ None — flat rules always apply | ✅ 4-state regime (TREND_BULL/BEAR, MEAN_REVERSION, CHOP) |
| **Entry indicators** | VWAP + Orderbook Skew only | RSI, BB, MACD, ATR, EMA(20/50), Funding, OI, Orderbook Imbalance |
| **Multi-timeframe** | ❌ None — single snapshot | ✅ 1H trigger + 4H trend confirmation |
| **Confidence model** | `|skew| / 0.8`, no spec lock-in | 0–100 score with per-regime profile gates |
| **Universe selection** | Fixed list from `settings.symbols` | Dynamic scorer: Volume + Momentum + Squeeze + Overextension penalty |
| **Position hold/exit** | No post-entry logic (TODO stubs) | Performance Gate (Meme/Standard/Core buckets + AI PositionJudge) |
| **Trailing stop** | ❌ Not implemented | ✅ ATR-based, regime-aware, 1-min cooldown |
| **Risk gate** | 3 gates: Liquidity, Spread, Drawdown | Same layers + profit lock + correlation + liquidity depth |
| **Kill switch** | SIGINT → cancel all | Full emergency close with DB audit trail |
| **Funding rate use** | ❌ Not wired into signal | ✅ Contrarian signal: negative funding = crowd short = buy |
| **Lead-lag** | Computed in `AlphaMetrics` but only stored to Redis | Not used (KCT is single-exchange execution) |
| **Execution SOR** | Post-Only → Reprice → Market | Post-Only → Reprice → Market + cancel-all safety |
| **State persistence** | Postgres via `StateManager` | Postgres via SQLAlchemy async |
| **Observability** | Prometheus metrics on `:8000` | Structured JSON logs + Prometheus metrics |

---

## 2. ASM Full Trade Lifecycle (Current State)

```
[Exchanges: Binance, OKX, Bybit]
          │ WebSocket orderbook
          ▼
┌─────────────────────────────────────────────────────┐
│  KEY 1 — Global Data Engine                         │
│  CCXTManager.watch_orderbook()                      │
│  Normalizer.normalize_orderbook() → ExchangeData    │
│  BadTickFilter (reject >5% in <1s)                  │
│  Normalizer.build_global_state()                    │
│    → GlobalVWAP (bid/ask mid × vol weighted)        │
│    → AggregateSkew = (bid_vol - ask_vol) / total    │
│  → Redis.set_global_state()                         │
└──────────────────────┬──────────────────────────────┘
                       │ poll every 1s
                       ▼
┌─────────────────────────────────────────────────────┐
│  KEY 2 — Alpha Bridge                               │
│  Redis.get_global_state(symbol)                     │
│  SignalGenerator.generate(symbol, vwap, skew)       │
│    - if skew > 0.3  → LONG                          │
│    - if skew < -0.3 → SHORT                         │
│    - else           → FLAT (no signal)              │
│    - confidence = min(|skew| / 0.8, 1.0)           │
│    - skip if confidence < 0.6                       │
│  → signal_queue.put(signal)                         │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  KEY 3 — Risk Gate                                  │
│  CircuitBreaker.is_halted() / is_paused()           │
│  RiskGate.evaluate(volume, bid, ask) ← ⚠️ HARDCODED │
│    Gate 1: volume_24h >= $1M                        │
│    Gate 2: spread <= 0.5%                           │
│    Gate 3: daily_pnl >= -2%                         │
│  → risk_queue.put(signal) if passed                 │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  KEY 4 — Bybit Executor (STUB)                      │
│  sor.cancel_all_positions() on kill                 │
│  executor_task: pulls from risk_queue               │
│  → state_manager.reconcile() only                  │
│  ⚠️ No actual order placement code                  │
└─────────────────────────────────────────────────────┘
```

### 🔴 Critical Gap in ASM: No Post-Entry Management
Once a signal reaches Key 4, there is **no position lifecycle** — no trailing stop, no performance checkpoint, no exit logic. The bot opens (if SOR were wired) but never closes intelligently.

---

## 3. KCT Full Trade Lifecycle (Reference Implementation)

```
[Bybit WebSocket + REST]
          │
          ▼
┌─────────────────────────────────────────────────────┐
│  UNIVERSE SELECTION (UniverseScorer)                │
│  Scores candidates 0–100:                          │
│    A. Volume score (0–30 pts)                       │
│    B. Early momentum 1H breakout (0–40 pts)         │
│    C. Overextension penalty (>30% move = -10–40)   │
│    D. Short-squeeze detector (0–30 pts)             │
│  Sector diversity cap (max 2 per sector)            │
│  → Top 12 candidates, min score 55                 │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  REGIME DETECTION (CryptoRegimeFilter)              │
│  Hurst Exponent (R/S method on BTC prices)          │
│    H > 0.5 → trending, H < 0.5 → mean-reverting   │
│  ADX on BTC 1H candles                              │
│    ADX > 25 → strong trend                          │
│  Price vs 200 EMA → BULL or BEAR direction          │
│  BTC Dominance (CoinGecko) → BTC season vs alt     │
│  → 4 states: TREND_BULL, TREND_BEAR,               │
│              MEAN_REVERSION, CHOP                   │
└──────────────────────┬──────────────────────────────┘
                       │  regime → StrategySelector
                       ▼
┌─────────────────────────────────────────────────────┐
│  SIGNAL GENERATION (CryptoAnalyst + TA Tools)      │
│  Full analysis per symbol:                          │
│    RSI(14), BB(20), EMA(20/50), MACD, ATR           │
│    Orderbook Imbalance (WebSocket live)             │
│    Funding rate (contrarian)                        │
│    Open Interest (confirms momentum)                │
│    4H trend confirmation (EMA trend + RSI)          │
│  LLM generates direction + confidence (0-100)       │
│  Trade memory injection (past trades)               │
│  Regime-adaptive confidence floors:                 │
│    Conservative: ≥70 | Semi-agg: ≥50 | Agg: ≥35   │
└──────────────────────┬──────────────────────────────┘
                       │  confidence >= threshold
                       ▼
┌─────────────────────────────────────────────────────┐
│  RISK GATE                                          │
│  Liquidity, Spread, Circuit Breaker                 │
│  Correlation risk (no 2 highly correlated pos)      │
│  Profit lock on winning positions                   │
│  Position manager: max concurrent positions         │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  EXECUTION (SOR)                                    │
│  Post-Only Limit → Reprice (3x) → Market IOC       │
│  Exchange-side SL placed immediately on fill        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  POST-ENTRY: Performance Gate (every 5 min)         │
│  Bucket assignment: Meme / Standard / Core          │
│  Checkpoint schedules:                              │
│    Meme:     15m, 30m, 1h, 2h, 4h, 8h, 24h        │
│    Standard: 1h, 4h, 12h, 24h, 72h                 │
│    Core:     4h, 24h, 72h, 7d                       │
│  Zone classification:                               │
│    HARD_FAIL  → immediate exit                      │
│    CLEAR_WIN  → hold + activate trailing stop       │
│    AMBIGUOUS  → PositionJudge AI (Tier 1 cheap)    │
│    DRAWDOWN   → PositionJudge AI (escalated)        │
│  Trailing Stop: ATR × regime multiplier             │
│  Consecutive 3× AI HOLDs on negative → force EXIT  │
└─────────────────────────────────────────────────────┘
```

---

## 4. Win-Rate Gap Analysis

### 4.1 Entry Quality

| Factor | ASM | KCT | Win-Rate Impact |
|:---|:---|:---|:---|
| Regime filter | ❌ Trades in CHOP | ✅ Halts in CHOP | **High** — CHOP destroys directional systems |
| Multi-TF confirmation | ❌ Single snapshot | ✅ 1H entry + 4H trend | **High** — entering against 4H trend kills R:R |
| Funding rate | ❌ Not used | ✅ Contrarian signal | **Medium** — strong crowding signal |
| Open Interest | ❌ Not used | ✅ Confirms momentum | **Medium** — filters fake breakouts |
| ATR volatility sizing | ❌ Fixed position size | ✅ Volatility-targeted | **High** — oversizing in high-ATR burns accounts |
| Universe selection | ❌ Fixed symbol list | ✅ Dynamic scored universe | **Medium** — miss early breakouts |
| Overextension guard | ❌ None | ✅ Penalty for >30% moves | **Medium** — avoids chasing tops |

### 4.2 Exit Quality

| Factor | ASM | KCT | Win-Rate Impact |
|:---|:---|:---|:---|
| Trailing stop | ❌ Not implemented | ✅ ATR-based | **Critical** — letting winners run |
| Performance checkpoints | ❌ None | ✅ Bucket-based schedule | **Critical** — cutting losers early |
| AI position judge | ❌ None | ✅ 2-tier (cheap + escalated) | **High** — prevents hope-holding |
| Consecutive hold guard | ❌ None | ✅ Force exit after 3× | **High** — eliminates zombie positions |
| Profit lock | ❌ None | ✅ Dynamic floor on gains | **Medium** — protects realized gains |

### 4.3 Risk Management

| Factor | ASM | KCT | Win-Rate Impact |
|:---|:---|:---|:---|
| Exchange-side SL on fill | ✅ Architecture mandated | ✅ Implemented | **Critical** — both correct |
| Correlation check | ❌ None | ✅ Prevents correlated losses | **Medium** |
| Real-time risk gate data | ❌ Hardcoded ($64k BTC) | ✅ Live from exchange | **High** — gate is currently meaningless |

---

## 5. Prioritized Improvement Roadmap for ASM (Win-Rate Focus)

### 🔴 Priority 1 — CRITICAL (Fix before any live trading)

**P1-A: Wire real bid/ask/volume into RiskGate**
```python
# app/main.py → risk_gate_task currently uses:
decision = risk_gate.evaluate(
    volume_24h=Decimal("5000000"),  # ← hardcoded
    bid_price=Decimal("64000"),     # ← hardcoded
    ask_price=Decimal("64100"),     # ← hardcoded
)
# Must pull from GlobalState in Redis instead
```

**P1-B: Implement exchange-side Stop Loss on fill**
- Currently mandated by `AGENTS.md` rule but no implementation exists in `app/execution/`

**P1-C: Add regime filter before signal generation**
- Implement `CryptoRegimeFilter` equivalent using Hurst + ADX on BTC
- Gate Alpha Bridge: emit FLAT in CHOP regime automatically

---

### 🟠 Priority 2 — HIGH (Directly increases win rate)

**P2-A: Add multi-timeframe confirmation**
- Alpha Bridge currently uses only the latest orderbook snapshot
- Add 15m rolling VWAP buffer (deque per symbol) as specified in `ALPHA_METRICS_SPEC.md §5`
- Require 4H EMA trend agreement before generating directional signals

**P2-B: Add funding rate as a signal component**
- Already in `DATA_MODEL.md` as `global_funding_avg`
- Implement contrarian interpretation: `funding < -0.0003` → bias LONG
- Wire into confidence score formula in `ALPHA_METRICS_SPEC.md §7`

**P2-C: Add ATR-based trailing stop in executor**
- Block needed in `app/execution/sor.py`
- Use ATR from 1H OHLCV as stop distance
- Amend Bybit exchange-side SL as price moves

---

### 🟡 Priority 3 — MEDIUM (Incremental improvement)

**P3-A: Add performance gate / checkpoints**
- Borrow KCT's checkpoint schedule concept
- Simplified: Meme (1h, 4h) / Standard (4h, 24h) / Core (24h, 72h)
- AMBIGUOUS zone → deterministic exit (no LLM needed for ASM)

**P3-B: Add open interest confirmation**
- Signal only fires if OI rising (confirms new money, not short covering)
- Requires CCXT `fetch_open_interest` call in CCXTManager

**P3-C: Dynamic universe scoring**
- Move from fixed `settings.symbols` to a scored universe
- Borrow `universe_scorer.py` scoring logic: Volume + 1H Momentum + Squeeze

---

### 🟢 Priority 4 — OPTIONAL (Future, KCT-level complexity)

**P4-A: AI PositionJudge** — LLM-powered hold/exit decisions (out of scope per `MVP_SCOPE.md`)

**P4-B: Sector diversity cap** — limit correlated crypto positions

**P4-C: Trade memory injection** — past trade outcomes injected into analyst prompt

---

## 6. Confidence Score Formula Proposal (ASM-specific)

Current ASM: `confidence = min(|skew| / 0.8, 1.0)` — single-signal, no spec lock-in.

**Proposed weighted composite (from `ALPHA_METRICS_SPEC.md §7`):**

$$\text{confidence} = w_1 \times \text{skew\_strength} + w_2 \times \text{lead\_lag\_strength} + w_3 \times \text{funding\_strength}$$

Where each strength = `min(1.0, |raw_value| / calibration_ceiling)`.

Starting weights: `w1 = 0.5, w2 = 0.3, w3 = 0.2` (skew weighted higher as most liquid signal).

**Regime modifier (borrow from KCT StrategySelector):**
- TREND_BULL: `confidence += 0.10`
- TREND_BEAR: `confidence -= 0.05`
- MEAN_REVERSION: `confidence -= 0.10`
- CHOP: `confidence = 0.0` (force FLAT)

---

## 7. What ASM Has That KCT Doesn't

| ASM Advantage | Status | Comment |
|:---|:---|:---|
| Multi-exchange VWAP (Binance + OKX + Bybit) | ✅ Implemented | Genuine structural edge over single-exchange |
| Lead-lag (Binance leads, Bybit lags) | ✅ Computed, not wired | The most unique signal in ASM — needs wiring |
| Bad tick filter | ✅ Implemented | KCT relies on CCXT handling this |
| Prometheus observability | ✅ Implemented | Better observability than KCT's logging |

> **The lead-lag signal is ASM's biggest differentiator.** Binance price movements predict Bybit fills 15–30 seconds later. This is the single signal KCT cannot replicate because it's single-exchange. It should be wired as the highest-weight component of the confidence score once calibrated.

---

## 8. Summary Verdict

| System | Entry Quality | Exit Quality | Risk Mgmt | Current Win-Rate Estimate |
|:---|:---|:---|:---|:---|
| ASM (current) | 🟡 Basic | 🔴 None | 🟡 Partial | Unknown — no execution |
| KCT (reference) | 🟢 Advanced | 🟢 Advanced | 🟢 Advanced | Live, iterated |

**Bottom line:** ASM's core data pipeline (multi-exchange VWAP + lead-lag) is architecturally superior for structural alpha extraction. But without a regime filter, a trailing stop, and a working risk gate (currently hardcoded), every signal generated is statistically a coin flip dressed up in infrastructure. The three P1 fixes are not feature requests — they are the difference between a system that produces P&L and one that produces logs.
