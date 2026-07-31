# Phase 5: Strategy Activation — Activate Carry, Liquidation, and Cross-Asset

**Impact:** 🔥🔥🔥  
**Effort:** Low (wiring, not new code — strategies already exist)  
**Risk:** Low (strategies already built and partially tested)

---

## The Problem: Built But Never Called

You have **three complete strategies** sitting dormant in your codebase. They're fully implemented, have proper risk profiles, and are even partially wired in `decision_engine.py` — but they're either never triggered or their prerequisites are never populated.

### 1. Funding Rate Carry (`evaluate_carry_signal`)

**Location:** [strategy_router.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/alpha/strategy_router.py)

**What it does:** Detects when funding rate has been consistently negative (or positive) for 3+ periods, indicating a structural carry opportunity. You get paid to hold the position.

**Why it's dormant:** 
- In the legacy `main.py` pipeline, it's never called at all
- In `decision_engine.py`, it IS called (line 616-629) but only for LONG direction
- It's guarded by `if self._redis is not None and direction == "LONG"` — **shorts with positive funding are excluded!**
- The Redis key `karsa:funding:history:{symbol}` must be populated by the data engine, which requires verifying it's actually being written

**Fix:**
```python
# decision_engine.py — activate for BOTH directions
# BEFORE:
if self._redis is not None and direction == "LONG":
    carry_bonus, carry_reason = await self._router.evaluate_carry_signal(...)

# AFTER:
if self._redis is not None:
    carry_bonus, carry_reason = await self._router.evaluate_carry_signal(
        symbol=symbol, direction=direction, 
        redis_client=self._redis, config=_s,
    )
```

**Expected impact:** In a CHOP market, carry signals add +15 to +25 score points. With the lowered CHOP gate (50 for carry, from Phase 2), this alone can generate 3-5 additional trades per day.

### 2. Liquidation Heatmap (`evaluate_liquidation_heatmap`)

**Location:** [strategy_router.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/alpha/strategy_router.py)

**What it does:** Detects when open interest has dropped sharply (≥5% in 1H), indicating forced liquidations are creating price dislocation. This is the "liquidity sweep" trade — the market over-extends to liquidate leveraged positions, then snaps back.

**Why it's dormant:**
- In `decision_engine.py`, it IS called (line 659-672)
- But it reads from `karsa:liq:cascade:{symbol}` and `karsa:oi:delta_1h:{symbol}` Redis keys
- **These keys are never written by the data engine!** The OHLCV fetcher doesn't compute OI deltas and store them in this format
- The liquidation cascade detection requires a separate data pipeline that doesn't exist yet

**Fix — Wire OI Delta Computation:**
```python
# In data engine or market_consumer.py — compute and store OI deltas

async def _publish_oi_delta(self, symbol: str, current_oi: float):
    """Compute and store 1H OI delta for liquidation heatmap strategy."""
    prev_key = f"karsa:oi:prev_1h:{symbol}"
    prev_oi_raw = await self._redis.get(prev_key)
    
    if prev_oi_raw:
        prev_oi = float(prev_oi_raw)
        if prev_oi > 0:
            delta_pct = (current_oi - prev_oi) / prev_oi
            await self._redis.set(
                f"karsa:oi:delta_1h:{symbol}",
                str(delta_pct),
                ex=3600,  # 1H TTL
            )
            
            # Detect cascade: >5% OI drop = liquidation event
            if delta_pct < -0.05:
                await self._redis.set(
                    f"karsa:liq:cascade:{symbol}",
                    json.dumps({
                        "delta_pct": delta_pct,
                        "oi_before": prev_oi,
                        "oi_after": current_oi,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    ex=1800,  # Valid for 30 minutes
                )
    
    await self._redis.set(prev_key, str(current_oi), ex=7200)
```

**Expected impact:** Liquidation sweeps happen 2-5 times per day across 150+ symbols. Each one is a high-R:R scalp opportunity (snap-back moves often cover 1-3x ATR).

### 3. Cross-Asset Momentum (`evaluate_cross_asset_momentum`)

**Location:** [strategy_router.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/alpha/strategy_router.py)

**What it does:** Checks if BTC, ETH, and the target altcoin are all moving in the same direction. When all three align, the probability of continuation is significantly higher.

**Why it's dormant:**
- In `decision_engine.py`, it IS called (line 678-691)
- But it reads from `karsa:momentum:btc`, `karsa:momentum:eth`, `karsa:momentum:{symbol}` Redis keys
- **These keys exist** (written by the data engine) but the format may not match what the strategy expects

**Fix:** Verify Redis key format matches strategy expectations. This is likely just a key name or JSON structure mismatch.

---

## New Strategy: Funding Term Structure (Already Partially Built)

The `decision_engine.py` already checks for `funding_term_signal` at lines 633-651:

```python
if term_signal == "SQUEEZE_IMMINENT_LONG" and direction == "LONG":
    score += 25
```

This is a **+25 score bonus** for detecting that funding rates are about to reverse (squeeze imminent). But the signal source (`karsa:market:{symbol}:funding_term_signal`) needs to be populated.

**Add to data engine:**
```python
async def _compute_funding_term_structure(self, symbol: str):
    """Compute predicted vs current funding for squeeze detection."""
    current_funding = await self._get_funding_rate(symbol)
    predicted_funding = await self._get_predicted_funding(symbol)
    
    if current_funding is not None and predicted_funding is not None:
        # If current is very negative but predicted is rising → squeeze imminent (LONG)
        if current_funding < -0.0003 and predicted_funding > current_funding * 0.5:
            signal = "SQUEEZE_IMMINENT_LONG"
        elif current_funding > 0.0003 and predicted_funding < current_funding * 0.5:
            signal = "SQUEEZE_IMMINENT_SHORT"
        else:
            signal = "NEUTRAL"
        
        await self._redis.set(
            f"karsa:market:{symbol}:funding_term_signal",
            signal, ex=3600,
        )
```

---

## Activation Checklist

| Strategy | Code Exists | Called | Data Source Exists | Fix Required |
|---|---|---|---|---|
| Carry Signal | ✅ | ⚠️ (LONG only) | ⚠️ (verify Redis keys) | Enable for SHORT; verify data pipeline |
| Liquidation Heatmap | ✅ | ✅ | ❌ (Redis keys empty) | Add OI delta computation to data engine |
| Cross-Asset Momentum | ✅ | ✅ | ⚠️ (verify format) | Verify key format match |
| Funding Term Structure | ✅ | ✅ | ❌ (Redis key empty) | Add term structure computation |
| HMM Regime Signal | ✅ | ✅ | ⚠️ (verify HMM loop) | Verify HMM classification loop runs |

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/consumer/decision_engine.py` | **MODIFY** | Enable carry for both LONG and SHORT |
| `app/consumer/market_consumer.py` | **MODIFY** | Add OI delta computation and publishing |
| `app/data/ccxt_manager.py` | **MODIFY** | Ensure OI data is fetched for all symbols |
| `app/consumer/live_loop.py` | **MODIFY** | Wire funding term structure computation |
| `app/main.py` | **MODIFY** | Wire carry/liquidation into legacy pipeline |
| `tests/test_carry_strategy.py` | **NEW** | Test carry signal detection |
| `tests/test_liquidation_heatmap.py` | **NEW** | Test OI cascade detection |

---

## Expected Impact

| Strategy | Trades/Day (est.) | Win Rate (est.) | Avg R:R | Notes |
|---|---|---|---|---|
| Carry | 3-5 | 55-65% | 0.5:1 | Low R:R but high win rate (structural edge) |
| Liquidation sweep | 2-4 | 40-50% | 2:1+ | High R:R, explosive snap-back moves |
| Cross-asset momentum | 1-3 | 50-55% | 1.5:1 | Confluence increases base signal quality |
| Funding squeeze | 1-2 | 45-55% | 2:1+ | Rare but very high conviction |
| **Total additional** | **7-14** | | | Added on top of existing signals |

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Carry trade loses on price movement | SL at 1x ATR; funding pays baseline 0.03%/day |
| Liquidation cascade continues (no snapback) | Tight SL at cascade low; max hold 15 minutes |
| Cross-asset signals are lagging | Require all 3 assets moving in last 15 min (not 1H) |
| OI data is unreliable | Validate against CCXT exchange API; fallback to no signal |
