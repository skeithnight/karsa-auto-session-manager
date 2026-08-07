# Phase 2: Regime Revolution — Make Every Regime Tradeable

**Impact:** 🔥🔥🔥🔥  
**Effort:** Medium (3-4 files, ~300 LOC)  
**Risk:** Medium (requires careful calibration per regime)

---

## The Problem

### Problem 1: The Regime Classifier Is a "Don't Trade" Classifier

Your `RegimeClassifier` uses ADX + Hurst + ATR to classify markets into: `TREND_BULL`, `TREND_BEAR`, `HYPER_BULL`, `HYPER_BEAR`, `RANGE`, `CHOP`.

**The real-world distribution of crypto assets (from empirical data):**

| Regime | % of Time | Your System's Response |
|---|---|---|
| CHOP (ADX < 20) | 35-45% | 0.3x size, 85+ gate, 30-min hold → **Barely trades** |
| RANGE (ADX 20-25) | 20-25% | 0.7x size, 65+ gate → **Trades conservatively** |
| TREND (ADX > 25) | 20-25% | 1.0x size, 65+ gate → **Trades normally** |
| HYPER (ADX > 40, extreme ATR) | 5-10% | 0.5x size, 15-min hold → **Micro-scalps only** |

**Result:** Your system is fully active only 20-25% of the time (TREND), partially active 20-25% (RANGE), and essentially dormant 40-55% of the time (CHOP + HYPER).

### Problem 2: No TRANSITION State

**The most profitable moments in crypto are regime transitions** — when the market shifts from CHOP to TREND, or from RANGE to HYPER. Your classifier detects these moments and says "regime changed from CHOP to TREND_BULL" — but by the time the regime is confirmed, the initial breakout move (often the most profitable part) is already over.

### Problem 3: Single-Timeframe Regime

Your regime is calculated on 1H candles. But a CHOP market on 1H might be a clear TREND on 15m (intra-candle structure) or a RANGE on 4H (higher timeframe context). You're using one resolution for all holding periods.

---

## The Solution

### A. Make CHOP Tradeable (Micro-Structure Strategies)

CHOP doesn't mean "no opportunity." It means "no trend." Profitable traders use CHOP for:

1. **Funding Rate Carry** — In CHOP, price is flat but funding rates still exist. If funding is -0.01%/8h, that's 0.03%/day of free carry on a long position with no directional risk. Over 30 days, that's ~1% — on leverage, that's significant.

2. **Range-Bound Mean Reversion** — CHOP markets often oscillate within a band. The band edges are tradeable:
   - Long at lower Bollinger Band with SL below the band
   - Short at upper Bollinger Band with SL above the band
   - Max hold: 2-4 hours (not 30 minutes — too short for mean reversion to work)

3. **Liquidity Sweep Scalps** — Market makers wipe stops below/above the range to fill large orders, then price snaps back. Your orderbook data can detect these:
   - Sudden spike in volume + price outside BB + immediate reversal = sweep
   - Entry on the snapback, SL at the sweep extreme

#### New CHOP Strategy Profiles

```python
# In dynamic_risk_gate.py — replace single CHOP profile with 3 sub-strategies

CHOP_CARRY = RiskProfile(
    regime="CHOP_CARRY",
    size_multiplier=Decimal("0.5"),     # Half size for carry (low risk)
    take_profit_type="FIXED",
    stop_loss_type="TIGHT",
    max_hold_time_mins=480,             # 8 hours (one funding period)
    use_post_only=True,
    trail_atr_mult=Decimal("1.0"),
    sl_atr_buffer=Decimal("1.0"),
)

CHOP_MEAN_REVERSION = RiskProfile(
    regime="CHOP_MEAN_REVERT",
    size_multiplier=Decimal("0.4"),
    take_profit_type="FIXED",
    stop_loss_type="TIGHT", 
    max_hold_time_mins=240,             # 4 hours (up from 30 min)
    use_post_only=True,
    trail_atr_mult=Decimal("1.5"),
    sl_atr_buffer=Decimal("1.0"),
)

CHOP_SWEEP_SCALP = RiskProfile(
    regime="CHOP_SWEEP",
    size_multiplier=Decimal("0.3"),
    take_profit_type="SCALP",
    stop_loss_type="MICRO",
    max_hold_time_mins=15,              # Quick in-and-out
    use_post_only=False,                # Market order for speed
    trail_atr_mult=Decimal("0.5"),
    sl_atr_buffer=Decimal("0.5"),
)
```

### B. Add TRANSITION Regime State

The `RegimeClassifier` should detect when the market is **transitioning** between states. This is the highest-EV moment:

```python
# In regime_classifier.py — add transition detection

class MarketRegime(str, Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    HYPER_BULL = "HYPER_BULL"
    HYPER_BEAR = "HYPER_BEAR"
    RANGE = "RANGE"
    CHOP = "CHOP"
    MEAN_REVERSION = "MEAN_REVERSION"
    # NEW: Transition states
    TRANSITION_BULL = "TRANSITION_BULL"    # CHOP/RANGE → TREND_BULL
    TRANSITION_BEAR = "TRANSITION_BEAR"    # CHOP/RANGE → TREND_BEAR
```

#### Transition Detection Logic

```python
def _detect_transition(self, current: MarketRegime, 
                        adx: float, adx_prev: float,
                        hurst: float, atr_percentile: float) -> MarketRegime | None:
    """Detect regime transition — the most profitable moment to trade."""
    
    # CHOP → TREND: ADX crossing above 20 with acceleration
    if current in (MarketRegime.CHOP, MarketRegime.RANGE):
        adx_acceleration = adx - adx_prev
        if adx > 18 and adx_acceleration > 2.0:  # ADX rising fast
            if hurst > 0.52:  # Trending characteristic emerging
                # Determine direction from price action
                if close > sma20:
                    return MarketRegime.TRANSITION_BULL
                else:
                    return MarketRegime.TRANSITION_BEAR
    
    return None
```

#### TRANSITION Risk Profile — Aggressive Entry

```python
TRANSITION_BULL = RiskProfile(
    regime="TRANSITION_BULL",
    size_multiplier=Decimal("1.2"),      # Larger than normal — this is the best setup
    take_profit_type="TRAILING",
    stop_loss_type="TIGHT",
    max_hold_time_mins=1440,             # 24 hours — let the trend develop
    use_post_only=False,                 # Speed matters more than fees
    trail_atr_mult=Decimal("2.5"),
    sl_atr_buffer=Decimal("1.2"),
)
```

**Why this is the highest-EV regime:** When a market transitions from CHOP to TREND, the breakout move often covers 2-5x ATR before the first pullback. Catching this transition early with appropriate sizing is the single most profitable trade type in crypto.

### C. Multi-Resolution Regime (15m + 1H + 4H)

Instead of a single 1H regime, compute regime at multiple timeframes and use the **holding-period-appropriate** one:

```python
class MultiResolutionRegime:
    """Compute regime at multiple timeframes for holding-period matching."""
    
    async def classify(self, symbol: str) -> dict[str, MarketRegime]:
        return {
            "15m": await self._classify_tf(symbol, "15m"),  # For scalps (CHOP trades)
            "1h":  await self._classify_tf(symbol, "1h"),   # For swings (RANGE/TREND)
            "4h":  await self._classify_tf(symbol, "4h"),   # For position trades
        }
    
    def get_regime_for_strategy(self, regimes: dict, strategy: str) -> MarketRegime:
        """Match regime resolution to strategy holding period."""
        if strategy in ("CHOP_SWEEP", "CHOP_MEAN_REVERT"):
            return regimes["15m"]  # Use 15m regime for short-hold strategies
        elif strategy in ("TREND_FOLLOW", "TRANSITION"):
            return regimes["4h"]   # Use 4H regime for longer holds
        else:
            return regimes["1h"]   # Default to 1H
```

### D. Lower the CHOP Gate Threshold

The current CHOP gate is 85 (out of 100). With the EV scoring model from Phase 1, CHOP strategies should use a gate proportional to their edge:

| CHOP Strategy | Gate Threshold | Rationale |
|---|---|---|
| CHOP_CARRY | 50 | Carry trades have a structural edge from funding rate — low signal quality needed |
| CHOP_MEAN_REVERSION | 60 | Mean reversion at BB extremes has clear invalidation (SL beyond band) |
| CHOP_SWEEP_SCALP | 70 | Liquidity sweeps need stronger confirmation (volume spike + snapback) |

Compare to current: **85 for all CHOP strategies** (effectively impossible to reach).

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/alpha/regime_classifier.py` | **MODIFY** | Add `TRANSITION_BULL`/`TRANSITION_BEAR` states + transition detection |
| `app/alpha/multi_resolution_regime.py` | **NEW** | 15m/1H/4H regime computation |
| `app/risk/dynamic_risk_gate.py` | **MODIFY** | Add CHOP sub-strategies + TRANSITION profiles |
| `app/consumer/decision_engine.py` | **MODIFY** | Use multi-resolution regime; lower CHOP gates |
| `app/alpha/strategy_router.py` | **MODIFY** | Add CHOP_CARRY, CHOP_MEAN_REVERT, CHOP_SWEEP scoring |
| `app/main.py` | **MODIFY** | Wire multi-resolution regime into alpha bridge |
| `tests/test_regime_transition.py` | **NEW** | Test transition detection boundary conditions |
| `tests/test_chop_strategies.py` | **NEW** | Test CHOP sub-strategy scoring and profiles |

---

## Validation Plan

1. **Backtest transition detection** against 6 months of BTC/ETH/SOL data
   - Target: detect ≥ 70% of breakouts within 2 bars of the real breakout
   - False positive rate: ≤ 30% (transitions that fail and revert to CHOP)

2. **Shadow mode CHOP strategies** for 2 weeks
   - Target: CHOP strategies produce 3-5 signals per day
   - Target: ≥ 40% win rate on CHOP carry, ≥ 35% on sweep scalps

3. **Transition trades** tracked separately in shadow mode
   - Target: ≥ 55% win rate with ≥ 2:1 R:R

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| CHOP carry trades lose on price movement | Tight SL (1x ATR) caps loss; funding pays ~0.03%/day baseline |
| False transition signals | Require ADX acceleration > 2.0 (not just ADX > 20); 2-bar hysteresis |
| Multi-resolution regime conflicts | Strategy selects appropriate resolution; conflicts are EV penalties not vetoes |
| Increased trade frequency overwhelms APM | APM already handles concurrent positions; max positions cap remains |
