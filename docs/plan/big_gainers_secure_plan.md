# Big Gainers Alpha — Secure Implementation Plan

> **Goal**: Capture 30-100%+ crypto runners while strictly ensuring zero increase in initial risk.
> **Strategy**: Better entry timing + asymmetric upside capture + reduced sizing on weak signals.
> **Supersedes**: `docs/plan/big_gainers_alpha_plan.md` (earlier draft, lacks phased security analysis)
> **Refined**: `docs/plan/big_gainers_refinement.md` — 3 corrections applied (volume math, CCXT SL, Pydantic alignment)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT PIPELINE                              │
│                                                                  │
│  UniverseScorer → StrategyRouter → RiskGate → AI → APM → Bybit  │
│       ↑                                              ↑          │
│   [MODIFY: add                 [MODIFY: add tiered exits]       │
│    volume anomaly,                                              │
│    dip-buy exemption]                                           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    NEW PIPELINE                                  │
│                                                                  │
│  UniverseScorer ───→ StrategyRouter ───→ DecisionEngine         │
│  [Volume Anomaly]    [Conviction Sizing]   [Dip Buyer Gate]     │
│       ↓                    ↓                     ↓               │
│  RiskGate ──────────→ AI Analyst ──────→ PortfolioRiskManager    │
│       ↓                                                        │
│  APM [Moon Bag Tranches] ─────────────→ BybitClient             │
│  [80% at 1.5R] [20% moon bag]                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Regime Conviction Scaling (Lowest Risk)

> **Why first**: Reduces risk on weak signals. No new entry logic, no execution changes.
> **Risk**: Zero increase — only REDUCES sizing on ambiguous regimes.

### 1.1 Add `conviction_score` to RegimeClassifier

**File**: `app/alpha/regime_classifier.py`

```python
# Add to classify() return value or create new method
def classify_with_conviction(self, features, snapshot) -> tuple[MarketRegime, float]:
    """Returns (regime, conviction_score) where conviction is 0.0-1.0."""
    regime = self.classify(features, snapshot)
    adx = features.adx_14 or 0.0
    hurst = features.hurst or 0.5

    # Conviction: how far above/below threshold
    if regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
        # ADX 25 = 0.0, ADX 40 = 1.0 (linear scale)
        conviction = max(0.0, min(1.0, (adx - 25.0) / 15.0))
    elif regime in (MarketRegime.HYPER_BULL, MarketRegime.HYPER_BEAR):
        conviction = 1.0  # Always full conviction
    elif regime == MarketRegime.RANGE:
        # Hurst 0.45 = 0.0, Hurst 0.3 = 1.0 (stronger MR = higher conviction)
        conviction = max(0.0, min(1.0, (0.45 - hurst) / 0.15))
    else:  # CHOP
        conviction = 0.3  # Low conviction — choppy market

    return regime, conviction
```

### 1.2 Store conviction in Redis alongside regime

**File**: `app/alpha/regime_classifier.py` → `run_classification_loop()`

```python
# In the payload dict, add:
payload = json.dumps({
    "regime": regime.value,
    "conviction": round(conviction, 3),  # NEW
    "adx": round(adx, 2),
    "hurst": round(hurst, 3),
    "atr_pct": round(atr_pct, 1),
})
```

### 1.3 Apply conviction to position sizing in DecisionEngine

**File**: `app/consumer/decision_engine.py` → `evaluate()`

```python
# After regime classification, read conviction from Redis
conviction = 1.0  # default
if self._redis:
    try:
        regime_data = json.loads(await self._redis.get("system:config:regime") or "{}")
        conviction = regime_data.get("conviction", 1.0)
    except Exception:
        pass

# After Kelly sizing, multiply by conviction
# This is the KEY safety lever: weak regimes → smaller positions
risk_pct = kelly_result * Decimal(str(conviction))
```

### 1.4 Tests

```python
# tests/alpha/test_regime_classifier.py
def test_conviction_trend_bull_weak():
    """ADX=26 should give ~0.07 conviction (barely trending)."""
    classifier = RegimeClassifier()
    # Mock features with ADX=26
    regime, conviction = classifier.classify_with_conviction(features, snapshot)
    assert regime == MarketRegime.TREND_BULL
    assert 0.0 <= conviction <= 0.15  # Very weak

def test_conviction_trend_bull_strong():
    """ADX=45 should give ~1.33 → capped at 1.0 conviction."""
    # Mock features with ADX=45
    regime, conviction = classifier.classify_with_conviction(features, snapshot)
    assert conviction == 1.0

def test_conviction_range_strong_mr():
    """Hurst=0.32 should give high conviction in RANGE."""
    # Mock features with Hurst=0.32
    regime, conviction = classifier.classify_with_conviction(features, snapshot)
    assert regime == MarketRegime.RANGE
    assert conviction > 0.7
```

---

## Phase 2: Volume Anomaly Detector (Early Detection)

> **Why second**: Gets you in BEFORE the move. No execution changes, only scoring.
> **Risk**: Zero increase — only adds bonus score to early-stage accumulation patterns.

### 2.1 Add volume anomaly detection to UniverseScorer

**File**: `app/data/universe_scorer.py`

```python
# Add new scoring component
VOLUME_ANOMALY_MAX = Decimal("25")  # 0-25 bonus

async def score_symbol(self, symbol: str) -> dict | None:
    # ... existing code ...

    # NEW: Volume Anomaly Detection (Refinement 1: corrected volume math)
    volume_anomaly_score = Decimal("0")
    if len(candles) >= 24:
        # Current 1H volume vs rolling 4H average (last 4 candles)
        # candles = [timestamp, open, high, low, close, volume]
        current_volume = Decimal(str(candles[-1][5]))  # latest 1H volume

        # FIX: Use last 4 candles for 4H average, not 24
        avg_volume_4h = sum(Decimal(str(c[5])) for c in candles[-4:]) / Decimal("4")

        if avg_volume_4h > 0:
            volume_ratio = current_volume / avg_volume_4h

            # Volume spiking but price hasn't moved yet = accumulation
            price_change_4h = abs((closes[-1] - closes[-4]) / closes[-4]) if closes[-4] > 0 else Decimal("0")

            if volume_ratio > Decimal("3") and price_change_4h < Decimal("0.05"):
                # 3x volume + <5% price move = whale accumulation
                volume_anomaly_score = min(
                    (volume_ratio - Decimal("3")) * Decimal("5"),
                    VOLUME_ANOMALY_MAX
                )
                logger.info(f"{symbol}: VOLUME ANOMALY detected — {volume_ratio:.1f}x avg, price stable")

    # Add to total score
    total = volume_score + momentum_score + overextension_penalty + squeeze_score + funding_score + volume_anomaly_score
    
    return {
        # ... existing fields ...
        "volume_anomaly_score": round(volume_anomaly_score, 2),
        "total_score": round(total, 2),
    }
```

### 2.2 Add `strong_performer` flag for Dip Buyer (prep for Phase 3)

```python
# In score_symbol(), add:
strong_performer = False
if len(closes) >= 48:
    price_48h_ago = closes[-48]
    if price_48h_ago > 0:
        move_48h = (closes[-1] - price_48h_ago) / price_48h_ago
        if move_48h > Decimal("0.15"):  # +15% in 48h
            strong_performer = True
            # DISABLE overextension penalty for strong performers
            overextension_penalty = Decimal("0")

return {
    # ... existing fields ...
    "strong_performer": strong_performer,
    "volume_anomaly_score": round(volume_anomaly_score, 2),
}
```

### 2.3 Tests

```python
# tests/data/test_universe_scorer.py
async def test_volume_anomaly_detection():
    """3x volume + stable price should trigger anomaly bonus."""
    # Mock: 24 candles, latest volume=3x avg, price change <5%
    score = await scorer.score_symbol("TEST/USDT")
    assert score["volume_anomaly_score"] > 0

async def test_strong_performer_exemption():
    """+15% in 48h should disable overextension penalty."""
    # Mock: price up 15% over 48 candles
    score = await scorer.score_symbol("STRONG/USDT")
    assert score["strong_performer"] is True
    assert score["overextension_penalty"] == 0
```

---

## Phase 3: Dip Buyer Gate (Winning Setup)

> **Why third**: Enters strong trends on pullbacks. Requires Phase 2 flags.
> **Risk**: LOW — only LOWERS gate threshold for dip-buy candidates, doesn't bypass risk checks.

### 3.1 Add dip-buy logic to DecisionEngine

**File**: `app/consumer/decision_engine.py` → `evaluate()`

```python
# After scoring, before gate check:
dip_buy_boost = False
if self._redis:
    try:
        universe_data = json.loads(await self._redis.get("system:universe:symbols") or "{}")
        symbol_scores = universe_data.get("scores", {})
        # Check if symbol has strong_performer flag from UniverseScorer
        # (Would need to store this in Redis during refresh)
        
        # For now, compute inline:
        if len(arr) >= 48:
            close_now = float(arr[-1][4])
            close_48h = float(arr[-48][4])
            if close_48h > 0:
                move_48h = (close_now - close_48h) / close_48h
                if move_48h > 0.15:  # +15% in 48h
                    # Check if current price is in dip zone (-5% to -10% from 48h high)
                    high_48h = max(float(arr[i][2]) for i in range(-48, 0))  # high prices
                    dip_from_high = (high_48h - close_now) / high_48h
                    if 0.05 <= dip_from_high <= 0.10:
                        dip_buy_boost = True
                        logger.info(f"evaluate: {symbol} DIP BUY candidate — {dip_from_high:.1%} pullback from 48h high")
    except Exception:
        pass

# Apply dip-buy boost (lower gate by 10%)
effective_gate = float(self._gate) * vol_factor
if dip_buy_boost:
    effective_gate *= 0.90  # 10% gate reduction
    logger.info(f"evaluate: {symbol} DIP BUY gate reduced to {effective_gate:.1f}")
```

### 3.2 Tests

```python
# tests/consumer/test_decision_engine.py
async def test_dip_buy_gate_reduction():
    """Strong performer in dip zone should get 10% gate reduction."""
    # Mock: +15% in 48h, current price -7% from high
    signal = await engine.evaluate(symbol, candles)
    # Should have lower gate threshold
```

---

## Phase 4: Moon Bag Tiered Exit (Let Winners Run)

> **Why last**: Modifies execution layer (APM). Most complex, highest risk.
> **Risk**: MEDIUM — changes position lifecycle. Must be thoroughly tested in shadow mode first.

### 4.1 Add tranche state to position tracking (Refinement 3: Pydantic alignment)

**File**: `app/models/position.py`

```python
# Add to existing Position Pydantic model
class Position(BaseModel):
    # ... existing fields ...
    tranche_state: str = "INITIAL"  # "INITIAL" | "MOON_BAG_ACTIVE"
    moon_bag_amount: Decimal | None = None
    moon_bag_sl: Decimal | None = None
    highest_since_partial: Decimal | None = None  # For trailing stop
```

**File**: `app/execution/position_manager.py`

```python
# Use Position model methods instead of flat dict
# position.tranche_state = "MOON_BAG_ACTIVE"
# position.amount = moon_bag_amount
# position.moon_bag_sl = entry_price
# await self._position_store.update(position)
```

### 4.2 Add tiered exit logic to _manage_single_position (Refinement 2+3: CCXT + Pydantic)

```python
async def _manage_single_position(self, position: Position) -> None:
    """Manage a single position: breakeven, trailing, time exit, moon bag.

    Uses Pydantic Position model for type-safe state management.
    """
    symbol = position.symbol
    side = position.side

    # ... existing breakeven/trailing logic ...

    # NEW: Moon Bag Tiered Exit
    if position.tranche_state == "INITIAL":
        entry_price = Decimal(str(position.entry_price))
        initial_risk = Decimal(str(position.initial_risk_per_unit))
        live_price = Decimal(str(position.live_price))
        amount = Decimal(str(position.amount))

        if entry_price > 0 and initial_risk > 0 and live_price > 0:
            # Calculate R-multiple
            if side == "LONG":
                r_multiple = (live_price - entry_price) / initial_risk
            else:
                r_multiple = (entry_price - live_price) / initial_risk

            # Trigger at +1.5R
            if r_multiple >= Decimal("1.5"):
                logger.warning(
                    f"APM: {symbol} {side} hit +{r_multiple:.2f}R — executing TIERED EXIT (80% closed, 20% moon bag)"
                )

                # Calculate close amount (80% of position)
                close_amount = (amount * Decimal("0.8")).quantize(Decimal("0.001"))
                moon_bag_amount = amount - close_amount

                try:
                    # 1. Execute 80% partial close FIRST (reduceOnly)
                    await self._client.create_order(
                        symbol=symbol,
                        type="market",
                        side="sell" if side == "LONG" else "buy",
                        amount=float(close_amount),
                        params={"reduceOnly": True}
                    )

                    # 2. Update local state IMMEDIATELY after successful close
                    position.tranche_state = "MOON_BAG_ACTIVE"
                    position.amount = moon_bag_amount
                    position.moon_bag_amount = moon_bag_amount

                    # 3. Set Breakeven SL for remaining 20% via CCXT standardized method
                    # Refinement 2: Use CCXT's set_stop_loss for Bybit V5 compatibility
                    try:
                        await self._client.set_stop_loss(
                            symbol=symbol,
                            stopLossPrice=float(entry_price)  # Breakeven
                        )
                        position.moon_bag_sl = entry_price
                        position.current_sl = str(entry_price)
                        position.stop_loss = str(entry_price)
                        logger.info(f"APM: Moon bag SL set to breakeven: {entry_price}")
                    except Exception as e:
                        # FAIL-SAFE: Close remaining 20% if SL placement fails
                        logger.critical(
                            f"APM: FAILED to set moon bag SL for {symbol}: {e}. "
                            f"Emergency closing remaining 20% to protect capital."
                        )
                        await self._client.create_order(
                            symbol=symbol,
                            type="market",
                            side="sell" if side == "LONG" else "buy",
                            amount=float(moon_bag_amount),
                            params={"reduceOnly": True}
                        )
                        return  # Exit management loop for this position

                    # 4. Persist via Pydantic model
                    await self._position_store.update(position)

                    logger.warning(
                        f"APM: {symbol} MOON BAG created — {moon_bag_amount} @ breakeven SL"
                    )

                    # Alert
                    if self._alert:
                        await self._alert.send(
                            f"🌙 {symbol} {side} MOON BAG: 80% closed at +{r_multiple:.1f}R, "
                            f"20% riding with breakeven SL"
                        )

                except Exception as e:
                    logger.error(f"APM: tiered exit FAILED for {symbol}: {e}")
                    # Don't update state — will retry next cycle

    # For moon bag positions, use ultra-wide trailing
    elif position.tranche_state == "MOON_BAG_ACTIVE":
        # Override trailing stop to 5x ATR (instead of normal 3x ATR)
        # This gives the moon bag room to breathe
        atr = Decimal(str(position.atr))
        if atr > 0:
            moon_trail_distance = atr * Decimal("5")
            # ... apply wider trailing logic ...
```

### 4.3 Add moon bag trailing stop logic (Refinement 2+3: CCXT + Pydantic)

```python
async def _manage_moon_bag_trailing(self, position: Position) -> None:
    """Ultra-wide trailing stop for moon bag positions.

    Uses CCXT standardized methods and Pydantic Position model.
    """
    symbol = position.symbol
    side = position.side
    live_price = Decimal(str(position.live_price))
    moon_bag_sl = Decimal(str(position.moon_bag_sl or "0"))
    atr = Decimal(str(position.atr))

    if atr <= 0 or live_price <= 0:
        return

    # Moon bag trailing: 5x ATR from highest price since partial close
    highest = position.highest_since_partial or Decimal("0")
    if live_price > highest:
        highest = live_price
        position.highest_since_partial = highest

    if side == "LONG":
        new_sl = highest - (atr * Decimal("5"))
        if new_sl > moon_bag_sl:
            moon_bag_sl = new_sl
    else:
        new_sl = highest + (atr * Decimal("5"))
        if new_sl < moon_bag_sl:
            moon_bag_sl = new_sl

    # Update exchange SL if improved
    if moon_bag_sl != Decimal(str(position.moon_bag_sl or "0")):
        try:
            # Use CCXT's standardized set_stop_loss (Refinement 2)
            await self._client.set_stop_loss(
                symbol=symbol,
                stopLossPrice=float(moon_bag_sl)
            )
            position.moon_bag_sl = moon_bag_sl
            position.current_sl = str(moon_bag_sl)
            position.stop_loss = str(moon_bag_sl)
            await self._position_store.update(position)
        except Exception as e:
            logger.error(f"APM: moon bag SL update FAILED for {symbol}: {e}")
```

### 4.4 Tests (Refinement 3: Pydantic model assertions)

```python
# tests/execution/test_position_manager.py
def test_moon_bag_trigger_at_1_5r():
    """Position at +1.5R should trigger 80/20 split."""
    position = create_test_position(entry=100, current=107.5, risk=5.0)
    # +7.5 / 5.0 = +1.5R
    await apm._manage_single_position(position)
    assert position.tranche_state == "MOON_BAG_ACTIVE"
    assert position.amount == Decimal("0.8")  # 80% of 1.0
    assert position.moon_bag_sl == Decimal("100")  # Breakeven

def test_moon_bag_sl_fail_safe():
    """If SL placement fails, remaining 20% should be emergency closed."""
    position = create_test_position(entry=100, current=107.5, risk=5.0)
    # Mock set_stop_loss to raise exception
    with patch.object(apm._client, 'set_stop_loss', side_effect=Exception("SL failed")):
        await apm._manage_single_position(position)
    # Should have emergency closed (amount = 0)
    assert position.amount == Decimal("0")

def test_moon_bag_wide_trailing():
    """Moon bag should use 5x ATR trailing (not 3x)."""
    position = create_test_position(tranche_state="MOON_BAG_ACTIVE", atr=2.0)
    await apm._manage_moon_bag_trailing(position)
    # Should trail 5x ATR = 10 points
```

---

## Phase 5: Shadow Mode Validation

> **Critical**: All phases MUST run in shadow mode for minimum 48 hours before live.

### 5.1 Shadow mode checklist

- [ ] Phase 1 (Conviction): Verify sizing scales with ADX/Hurst
- [ ] Phase 2 (Volume Anomaly): Verify anomaly detection triggers correctly
- [ ] Phase 3 (Dip Buyer): Verify gate reduction applies only to valid candidates
- [ ] Phase 4 (Moon Bag): Verify 80/20 split executes at +1.5R, moon bag trails correctly
- [ ] No phantom positions created
- [ ] No race conditions in tranche state updates
- [ ] All exchange-side SLs placed correctly

### 5.2 Shadow metrics to monitor

```
# Conviction scaling
regime_conviction_avg
position_size_vs_conviction_correlation

# Volume anomaly
volume_anomaly_signals_total
volume_anomaly_entries_total

# Dip buyer
dip_buy_candidates_total
dip_buy_gate_reductions_total

# Moon bag
moon_bag_triggers_total
moon_bag_partial_closes_total
moon_bag_sl_updates_total
moon_bag_final_exits_total
```

---

## Phase 6: Live Deployment (Gradual Rollout)

### 6.1 Deployment order

1. **Conviction Scaling** (Phase 1) → Deploy immediately, zero risk increase
2. **Volume Anomaly** (Phase 2) → Deploy after 24h shadow validation
3. **Dip Buyer** (Phase 3) → Deploy after 48h shadow validation
4. **Moon Bag** (Phase 4) → Deploy after 72h shadow validation (most complex)

### 6.2 Rollback triggers

| Trigger | Action |
|---------|--------|
| Moon bag SL not placed within 5s of partial close | Disable moon bag, alert |
| Conviction scaling causes >20% position size reduction | Review ADX thresholds |
| Volume anomaly false positive rate >50% | Increase volume ratio threshold |
| Any exchange-side SL failure | Immediate circuit breaker |

### 6.3 Feature flags (Redis)

```python
# config/features/big_gainers.yaml
conviction_scaling_enabled: true      # Phase 1
volume_anomaly_enabled: true          # Phase 2
dip_buyer_enabled: true               # Phase 3
moon_bag_enabled: false               # Phase 4 (disabled until shadow validated)

# Tuning parameters
moon_bag_close_pct: 0.80              # 80% closed at +1.5R
moon_bag_trail_atr_mult: 5.0          # Ultra-wide trailing for moon bag
conviction_adx_floor: 25.0            # ADX below this = 0 conviction
conviction_adx_ceiling: 40.0          # ADX above this = 1.0 conviction
volume_anomaly_ratio: 3.0             # 3x average = anomaly
dip_buyer_pullback_min: 0.05          # 5% pullback from high
dip_buyer_pullback_max: 0.10          # 10% pullback from high
```

---

## Risk Matrix (Validated by Refinement Review)

| Phase | Risk Level | Why | Mitigation | Verdict |
|-------|-----------|-----|------------|---------|
| 1: Conviction | 🟢 ZERO | Only REDUCES sizing | Mathematically bounded [0,1] | ✅ APPROVED |
| 2: Volume Anomaly | 🟢 ZERO | Only ADDS bonus score | Doesn't bypass any gates | ✅ APPROVED (Refinement 1) |
| 3: Dip Buyer | 🟡 LOW | LOWERS gate threshold | Still passes all risk checks | ✅ APPROVED |
| 4: Moon Bag | 🟠 MEDIUM | Changes execution | Shadow-only for 72h minimum | ✅ APPROVED (Refinements 2+3) |

---

## Open Questions (Resolved)

1. **Exchange Support**: Bybit supports partial closes via multiple TP orders. ✅
2. **Moon Bag Sizing**: 80/20 split confirmed. ✅
3. **Conviction Formula**: Linear scale from ADX/Hurst. ✅

---

## Definition of Done

- [ ] All 4 phases implemented with tests
- [ ] Shadow mode validation complete (72h minimum)
- [ ] No increase in initial risk (verified via metrics)
- [ ] Moon bag SL placed within 5s of partial close (verified via logs)
- [ ] Conviction scaling reduces sizing on weak regimes (verified via metrics)
- [ ] Volume anomaly detection triggers on accumulation patterns (verified via backtest)
- [ ] Dip buyer gate reduction only applies to valid candidates (verified via unit tests)
- [ ] All exchange-side SLs placed correctly (no in-memory-only tracking)
- [ ] No phantom positions created
- [ ] No race conditions in tranche state updates
- [ ] **Refinement 1**: Volume math uses candles[-4:] (4H average) not candles[-24:]
- [ ] **Refinement 2**: Moon bag SL uses CCXT's set_stop_loss with emergency fail-safe
- [ ] **Refinement 3**: Position state uses Pydantic model, not flat dicts
- [ ] **Refinement 2**: Emergency close if SL placement fails (capital protection)
