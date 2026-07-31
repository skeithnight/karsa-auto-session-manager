# Phase 4: Sizing & Edge — Wire Kelly + Per-Asset Calibration

**Impact:** 🔥🔥🔥  
**Effort:** Medium (3-4 files, ~250 LOC)  
**Risk:** Low (Kelly sizer already exists, just not wired properly)

---

## The Problem

### Problem 1: Kelly Sizer Exists But Was Dead Code

Your [kelly_sizer.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/risk/kelly_sizer.py) is beautifully written — Fractional Kelly with drawdown-adaptive sizing, uncertainty adjustment, and GARCH volatility targeting. **Three sprints of work.**

But in the legacy `main.py` pipeline, position sizing is:
```python
amount = (available * dynamic_risk) / price
# Where dynamic_risk = 0.03 (hardcoded 3%)
```

Kelly is completely bypassed. Your 3-sprint sizing system does nothing.

> **Good news:** The `consumer/decision_engine.py` pipeline DOES wire Kelly properly (lines 1111-1237). But the legacy pipeline in `main.py` doesn't. If you're running on `main.py`, Kelly is dead code.

### Problem 2: One Size Fits All Microstructure

Your signal normalization uses hardcoded constants for ALL 411 symbols:

```python
# In signals.py
s_skew = max(-1.0, min(1.0, aggregate_skew / 0.8))        # 0.8 for all symbols
s_lead_lag = max(-1.0, min(1.0, lead_lag_delta / 0.0003))  # 0.0003 for all symbols
s_funding = max(-1.0, min(1.0, funding_rate / 0.0005))     # 0.0005 for all symbols
```

BTC/USDT has $500M daily volume. HEMI/USDT has $2M. Their microstructure is **completely different:**

| Metric | BTC/USDT | HEMI/USDT | Implication |
|---|---|---|---|
| Typical skew | ±0.3 | ±2.0 | HEMI skew of 0.8 is nothing; for BTC it's a signal |
| Lead-lag delta | ±0.0001 | ±0.001 | HEMI's lag is 10x; same threshold is meaningless |
| Funding rate | ±0.001 | ±0.01 | HEMI funding is 10x; 0.0005 threshold catches nothing |
| Spread | 0.01% | 0.1-0.5% | HEMI spread eats the entire move |

Using the same normalization constants is like using the same shoe size for a child and an adult.

---

## The Solution

### A. Ensure Kelly Is Always Active

#### Step 1: Deprecate Legacy Sizing in `main.py`

The `main.py` executor_task uses a hardcoded `0.03` risk:
```python
# main.py:1069 — REPLACE THIS
amount = (available * dynamic_risk) / price  # dynamic_risk = 0.03
```

Replace with Kelly-based sizing that matches `decision_engine.py`:

```python
# main.py — use KellySizer for all trades
kelly_sizer = KellySizer()

# Fetch trade history for this symbol
wins, losses, avg_win, avg_loss = await _get_symbol_stats(trade_store, symbol)

risk_pct = kelly_sizer.calculate_risk_pct(
    wins=wins, losses=losses,
    avg_win_usd=avg_win, avg_loss_usd=avg_loss,
    fallback_score=signal.confidence * 100,
)

# Apply drawdown adaptation
risk_pct = kelly_sizer.apply_drawdown_adaptive(
    risk_pct, current_equity, equity_peak
)

# Apply regime multiplier
risk_pct *= regime_profile.size_multiplier

# Calculate amount from risk distance (SL-based)
sl_distance = abs(signal.entry_price - signal.sl_price)
if sl_distance > 0:
    amount = (available * risk_pct) / sl_distance
else:
    amount = available * risk_pct / price  # fallback
```

### B. Per-Asset Microstructure Calibration

Create a calibration service that computes per-symbol normalization constants from rolling historical data:

#### File: `app/data/asset_calibrator.py` [NEW]

```python
class AssetCalibrator:
    """Computes per-symbol normalization constants from rolling 30-day data."""
    
    # Redis key pattern: karsa:calibration:{symbol}
    CALIBRATION_TTL = 3600 * 4  # Refresh every 4 hours
    
    @dataclass
    class CalibrationProfile:
        symbol: str
        skew_95pct: float          # 95th percentile of |skew|
        lead_lag_95pct: float      # 95th percentile of |lead_lag_delta|
        funding_95pct: float       # 95th percentile of |funding_rate|
        spread_median: float       # Median spread (for quality scoring)
        atr_median: float          # Median ATR (for volatility normalization)
        volume_24h_avg: float      # Avg 24h volume (for liquidity scoring)
        updated_at: datetime
    
    async def calibrate_symbol(self, symbol: str, 
                                ohlcv_fetcher: OHLCVFetcher,
                                redis_client: RedisClient) -> CalibrationProfile:
        """Compute calibration from rolling 30-day data."""
        
        # Fetch 30 days of 1H candles
        candles = await ohlcv_fetcher.fetch(symbol, "1h", 720)
        
        # Compute 95th percentiles for normalization
        skews = []  # collected from Redis history
        funding_rates = []  # collected from Redis history
        
        # ATR and spread from candle data
        atrs = self._rolling_atr(candles, period=14)
        spreads = self._rolling_spreads(candles)  # from high-low ratio
        
        profile = CalibrationProfile(
            symbol=symbol,
            skew_95pct=np.percentile(np.abs(skews), 95) if skews else 0.8,
            lead_lag_95pct=0.0003,  # default until we collect enough data
            funding_95pct=np.percentile(np.abs(funding_rates), 95) if funding_rates else 0.0005,
            spread_median=np.median(spreads) if spreads else 0.001,
            atr_median=float(np.median(atrs)) if atrs else 0.01,
            volume_24h_avg=self._avg_daily_volume(candles),
            updated_at=datetime.now(timezone.utc),
        )
        
        # Cache in Redis
        await redis_client.set(
            f"karsa:calibration:{symbol}",
            json.dumps(asdict(profile), default=str),
            ex=self.CALIBRATION_TTL,
        )
        
        return profile
    
    async def get_profile(self, symbol: str, redis_client) -> CalibrationProfile:
        """Get cached calibration or use defaults."""
        raw = await redis_client.get(f"karsa:calibration:{symbol}")
        if raw:
            return CalibrationProfile(**json.loads(raw))
        return self._default_profile(symbol)
```

#### Integrate into SignalGenerator

```python
# In signals.py — use per-asset normalization
async def generate(self, symbol, aggregate_skew, lead_lag_delta, 
                   funding_rate, oi_change_pct, calibration: CalibrationProfile):
    
    # BEFORE (hardcoded):
    # s_skew = max(-1.0, min(1.0, aggregate_skew / 0.8))
    
    # AFTER (per-asset):
    s_skew = max(-1.0, min(1.0, aggregate_skew / calibration.skew_95pct))
    s_lead_lag = max(-1.0, min(1.0, lead_lag_delta / calibration.lead_lag_95pct))
    s_funding = max(-1.0, min(1.0, funding_rate / calibration.funding_95pct))
```

### C. Confidence-Proportional Sizing via Kelly

The Kelly fraction should scale with signal confidence:

```python
# In decision_engine.py _build_signal()
# Current: Kelly uses trade history only
# New: Kelly × confidence scaling

base_kelly = kelly_sizer.calculate_risk_pct(wins, losses, avg_win, avg_loss)

# Confidence scaling: high-confidence signals get more capital
confidence_mult = 0.5 + (score / 100.0)  # 0.5x at score=0, 1.5x at score=100
scaled_risk = base_kelly * Decimal(str(confidence_mult))

# Apply regime + drawdown adjustments on top
scaled_risk *= profile.size_multiplier
scaled_risk = kelly_sizer.apply_drawdown_adaptive(scaled_risk, equity, peak)
```

### D. EV Weight Auto-Calibration (Phase 1 Connection)

Use backtest data to automatically calibrate the EV scoring weights from Phase 1:

```python
class EVWeightCalibrator:
    """Auto-calibrate EV component weights from historical trade outcomes."""
    
    async def calibrate(self, trade_history: list[dict]) -> dict[str, float]:
        """Logistic regression on trade outcomes to find optimal weights."""
        
        # Features: the EV components at time of entry
        # Label: 1 if trade was profitable, 0 if not
        
        X = []  # EV component vectors
        y = []  # Outcomes
        
        for trade in trade_history:
            components = trade.get("ev_components", {})
            if not components:
                continue
            X.append([components.get(k, 0.5) for k in EVScorer.WEIGHTS.keys()])
            y.append(1 if trade["realized_pnl"] > 0 else 0)
        
        if len(X) < 50:
            return EVScorer.WEIGHTS  # Not enough data yet
        
        # Logistic regression to find weights that predict profitability
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression()
        model.fit(X, y)
        
        # Normalize coefficients to sum to 1
        coeffs = np.abs(model.coef_[0])
        weights = coeffs / coeffs.sum()
        
        return dict(zip(EVScorer.WEIGHTS.keys(), weights))
```

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/data/asset_calibrator.py` | **NEW** | Per-symbol microstructure calibration |
| `app/alpha/ev_weight_calibrator.py` | **NEW** | Auto-calibrate EV weights from backtest |
| `app/alpha/signals.py` | **MODIFY** | Use CalibrationProfile instead of hardcoded constants |
| `app/main.py` | **MODIFY** | Replace hardcoded `0.03` with Kelly-based sizing |
| `app/consumer/decision_engine.py` | **MODIFY** | Add confidence-proportional sizing |
| `tests/test_asset_calibrator.py` | **NEW** | Test calibration computation and caching |
| `tests/test_kelly_integration.py` | **NEW** | Test Kelly is actually called in both pipelines |

---

## Validation Plan

1. **Kelly sizing impact** — Compare simulated P&L with flat 3% sizing vs Kelly:
   - Target: Kelly produces ≥ 15% better risk-adjusted returns (higher Sharpe)
   - Target: Max drawdown reduced by ≥ 20%

2. **Per-asset calibration** — Compare signal quality before/after calibration:
   - Target: Win rate improves ≥ 5% on low-liquidity altcoins
   - Target: False signals on BTC/ETH reduced ≥ 10%

3. **Weight calibration** — Run on 6 months of backtest data:
   - Target: Calibrated weights improve out-of-sample Sharpe by ≥ 0.3

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Kelly over-bets on small sample | `MIN_TRADES = 15` floor; uncertainty adjustment halves Kelly on small samples |
| Calibration data is stale | 4-hour TTL with fallback to conservative defaults |
| Weight calibration overfits | Walk-forward validation; weights bounded to [0.05, 0.35] per component |
| Confidence scaling amplifies bad trades | Drawdown-adaptive sizing caps total risk; exchange-side SL limits loss |
