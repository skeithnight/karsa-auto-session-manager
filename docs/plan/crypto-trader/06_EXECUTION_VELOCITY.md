# Phase 6: Execution Velocity — Fix TTL, Latency, and Throughput Blockers

**Impact:** 🔥🔥  
**Effort:** Low (config changes + minor refactors)  
**Risk:** Low (no new logic, just unblocking existing paths)

---

## The Problem

Even after Phases 1-5 generate more signals, several **execution bottlenecks** prevent signals from becoming actual trades. These are "last mile" problems that silently kill profitable opportunities.

---

## Fix 1: Signal TTL Is Too Short (5 Seconds)

**Location:** [main.py:331](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py#L331)

```python
SIGNAL_TTL_S = 5  # Seconds before a queued signal expires
```

**The timing:**
- Alpha loop: 1 second per cycle
- Risk gate processing: 1-3 seconds (PRM + gates + checks)
- SOR execution (Post-Only + reprice): 2-6 seconds
- **Total path: 4-10 seconds**

A 5-second TTL means **50-100% of signals expire before execution.** The signal is generated, passes all filters, gets queued, and then... times out waiting for the executor to pick it up.

**Fix:**
```python
SIGNAL_TTL_S = 30  # 30 seconds — covers worst-case execution path
```

**Why 30 and not 60:** Crypto prices move fast. A signal older than 30 seconds is based on stale microstructure data (skew, depth, spread may have changed). 30 seconds is the sweet spot between "don't expire too early" and "don't trade on stale data."

---

## Fix 2: Max Signals Per Cycle Is Too Low

**Location:** [main.py:334](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py#L334)

```python
MAX_SIGNAL_PER_CYCLE = 5
```

With 411 symbols being scanned every cycle, capping at 5 signals means that if 20 symbols have valid setups (e.g., a market-wide momentum push), you only trade the first 5 and miss 15.

**Fix:**
```python
MAX_SIGNAL_PER_CYCLE = 15  # Allow more candidates; PRM caps actual positions
```

The `max_positions` setting (default 5) already prevents over-trading. `MAX_SIGNAL_PER_CYCLE` should be **3x max positions** to allow for signals that get rejected by PRM/execution and ensure the best signals reach the executor.

---

## Fix 3: Whipsaw Cooldown Is Too Aggressive

**Location:** [main.py:439-454](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py#L439-L454)

```python
WHIPSAW_COOLDOWN_S = 2700  # 45 minutes cooldown after a loss
```

If you lose money on SOL/USDT, you can't trade SOL again for **45 minutes.** But markets are regime-dependent — a loss in one microstructure doesn't mean the next 45 minutes are bad. The loss might have been from a wick that's already recovered.

**Fix: Replace time-based cooldown with condition-based re-entry:**

```python
# Instead of: "Don't trade SOL for 45 minutes after a loss"
# Use: "Don't trade SOL until microstructure has reset"

class SmartCooldown:
    """Re-entry allowed when microstructure has shifted, not by time alone."""
    
    def should_block(self, symbol: str, last_loss_time: float,
                     current_regime: str, loss_regime: str,
                     current_skew: float, loss_skew: float) -> bool:
        
        elapsed = time.time() - last_loss_time
        
        # Minimum cooldown: 5 minutes (let the dust settle)
        if elapsed < 300:
            return True
        
        # If regime has changed since the loss, allow re-entry
        if current_regime != loss_regime:
            return False
        
        # If skew has flipped direction, the setup is different
        if (current_skew > 0 and loss_skew < 0) or (current_skew < 0 and loss_skew > 0):
            return False
        
        # Hard cap: 20 minutes max cooldown (not 45)
        if elapsed > 1200:
            return False
        
        return True  # Same regime, same direction, < 20 min → still cooling
```

---

## Fix 4: Signal Cooldown Per Symbol (45s → Adaptive)

**Location:** [main.py:443-449](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py#L443-L449)

```python
SIGNAL_COOLDOWN_S = 45  # Can't signal same symbol within 45 seconds
```

45 seconds is too long for micro-scalp strategies (CHOP regime, 15-min max hold). If a sweep happens, you need to re-enter within seconds.

**Fix: Make cooldown regime-dependent:**
```python
SIGNAL_COOLDOWNS = {
    "CHOP": 10,        # Fast regime, fast cooldown
    "RANGE": 30,       # Medium regime
    "TREND": 60,       # Slow regime, avoid over-trading the trend
    "TRANSITION": 15,  # Breakout — need to add quickly if first entry failed
    "HYPER": 5,        # Extreme moves — every second matters
}
```

---

## Fix 5: Price Deviation Guard Is Too Tight

**Location:** [main.py:700-711](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py#L700-L711)

```python
PRICE_DEVIATION_PCT = Decimal("0.005")  # 0.5% max price change
```

This checks if the price has moved more than 0.5% since signal generation. In HYPER regimes, price can move 0.5% in **1 second.** This guard is effectively blocking all HYPER regime trades.

**Fix: Make deviation regime-dependent:**
```python
PRICE_DEVIATION_LIMITS = {
    "CHOP": Decimal("0.003"),       # 0.3% — tight in low-vol
    "RANGE": Decimal("0.005"),      # 0.5% — standard
    "TREND": Decimal("0.008"),      # 0.8% — trends move
    "HYPER_BULL": Decimal("0.015"), # 1.5% — rapid moves are expected
    "HYPER_BEAR": Decimal("0.015"),
    "TRANSITION": Decimal("0.010"), # 1.0% — breakout volatility
}
```

---

## Fix 6: `main.py` Monolith → Clean Pipeline Separation

`main.py` is 1,870 lines containing signal generation, risk checking, execution, regime classification, reconciliation, and startup. This makes debugging at 3 AM nearly impossible.

**Refactor plan:**

```
main.py (1870 LOC) → Split into:
├── main.py           (~200 LOC)  # Startup, wiring, shutdown only
├── pipeline/
│   ├── alpha.py      (~200 LOC)  # alpha_bridge_task
│   ├── risk.py       (~150 LOC)  # risk_gate_task
│   ├── executor.py   (~300 LOC)  # executor_task
│   ├── regime.py     (~100 LOC)  # regime_engine_task
│   └── reconciler.py (~150 LOC)  # position_reconciler_task, trade_history_reconciler
```

**Benefits:**
- Each file has one responsibility
- Debugging traces are shorter and clearer
- Easier to A/B test pipeline stages independently
- Cleaner git blame and code review

> [!NOTE]
> This refactor doesn't change any logic. It's a pure structural reorganization. Test coverage before and after must be identical.

---

## Fix 7: Blocked Hours Are Too Broad

**Location:** [entry_filter.py:44](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/alpha/entry_filter.py#L44)

```python
blocked_hour_start: int = 3,   # Block 03:00-05:00 UTC
blocked_hour_end: int = 5,
```

AND in `decision_engine.py:315`:
```python
if _s.session_block_start_hour <= current_hour < _s.session_block_end_hour:
    # Block altcoin entries
```

Two overlapping time blocks in different files! The entry filter blocks 3-5 UTC, and the decision engine has a configurable session block. This double-block means:
- Altcoins are completely blocked during Asian session
- BTC/ETH might still trade (if `session_block_allow_btc_eth` is set)

**Fix: Consolidate into one configurable session manager:**
```python
# Single source of truth for session activity
class SessionActivityManager:
    """Unified session quality scoring — replaces double time blocks."""
    
    def get_session_config(self, hour_utc: int, symbol: str) -> SessionConfig:
        if 0 <= hour_utc < 3:
            # Late Asia: low liquidity, but BTC still trades
            return SessionConfig(
                sizing_mult=0.5 if symbol not in ("BTC/USDT", "ETH/USDT") else 0.8,
                min_ev_threshold=0.65,
                blocked=False,  # Never fully block — just reduce size
            )
        elif 3 <= hour_utc < 6:
            # Dead zone: minimum activity
            return SessionConfig(
                sizing_mult=0.3,
                min_ev_threshold=0.75,
                blocked=False,  # Still allow exceptional setups
            )
        elif 12 <= hour_utc < 16:
            # LDN/NY overlap: maximum liquidity
            return SessionConfig(
                sizing_mult=1.2,
                min_ev_threshold=0.50,
                blocked=False,
            )
        # ... etc
```

---

## Summary of All Fixes

| Fix | Current | After | Impact |
|---|---|---|---|
| Signal TTL | 5 seconds | 30 seconds | 2-3x more signals reach executor |
| Max signals/cycle | 5 | 15 | Captures market-wide moves |
| Whipsaw cooldown | 45 min flat | 5-20 min adaptive | 2x faster re-entry |
| Signal cooldown | 45s flat | 5-60s by regime | CHOP scalps can repeat faster |
| Price deviation | 0.5% flat | 0.3-1.5% by regime | HYPER trades not killed |
| Blocked hours | Hard block 3-5 UTC | Soft sizing reduction | Exceptional setups still trade |
| main.py monolith | 1870 LOC | 200 LOC + 5 modules | Debugging speed 5x |

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/main.py` | **MODIFY** | Update TTL, cooldowns, deviation limits; extract pipeline modules |
| `app/pipeline/alpha.py` | **NEW** | Extracted alpha_bridge_task |
| `app/pipeline/risk.py` | **NEW** | Extracted risk_gate_task |
| `app/pipeline/executor.py` | **NEW** | Extracted executor_task |
| `app/pipeline/regime.py` | **NEW** | Extracted regime_engine_task |
| `app/pipeline/reconciler.py` | **NEW** | Extracted reconciliation tasks |
| `app/alpha/smart_cooldown.py` | **NEW** | Condition-based cooldown (replaces flat timer) |
| `app/alpha/session_activity.py` | **NEW** | Unified session quality manager |
| `app/alpha/entry_filter.py` | **MODIFY** | Remove time-of-day block (moved to session manager) |
| `app/consumer/decision_engine.py` | **MODIFY** | Use session manager; adaptive cooldowns |

---

## Validation Plan

1. **TTL fix** — Measure signal-to-execution rate before/after
   - Target: ≥ 80% of queued signals reach executor (up from ~50%)

2. **Cooldown fix** — Track re-entry trades after cooldown expires
   - Target: Re-entry trades have ≥ 40% win rate (not noise)

3. **Pipeline extraction** — All existing tests pass without modification
   - Target: Zero test failures; identical behavior

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| 30s TTL allows stale signals | Price deviation guard catches actual staleness |
| More signals = more risk | max_positions cap unchanged; PRM gates unchanged |
| Shorter cooldowns = whipsaw losses | Condition-based cooldown requires regime/skew shift |
| Pipeline extraction introduces bugs | No logic changes; pure structural refactor |
