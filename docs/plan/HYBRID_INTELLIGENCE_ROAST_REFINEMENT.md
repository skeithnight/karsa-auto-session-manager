--- HYBRID_INTELLIGENCE_ROAST_AND_REFINEMENT.md (原始)

+++ HYBRID_INTELLIGENCE_ROAST_AND_REFINEMENT.md (修改后)

# Hybrid Intelligence Crypto Trading System: Roast & Refinement Plan

**Date:** 2026-07-28
**Branch:** `feat/quant-trader-persona-refactor`
**Reviewer:** Quantitative Trading Systems Analyst
**Scope:** Code-only review focusing on edge quality, hybrid intelligence integration, and production readiness

---

## Executive Summary: The Brutal Truth

You've built a **hybrid intelligence system** that looks like a quant fund's dream on paper but behaves like a retail bot with an identity crisis in practice. The architecture is clean—modular edge families, separated scoring, isolated sizing pipelines—but underneath the polished abstractions lies a fundamental problem:

**You're mixing statistical signal processing with heuristic magic numbers, then pretending the result is "hybrid intelligence."**

The system has three distinct personalities fighting for control:

1. **The Quant** (Kelly sizing, GARCH volatility, regime classification)
2. **The Heuristic Trader** (hardcoded funding thresholds, UTC session multipliers, score stacking)
3. **The AI Researcher** (ML prefilter, similarity-based EV, unrecorded confidence scores)

None of them are winning. The result is a system that's too complex to debug, too heuristic to trust, and too constrained to capture real alpha.

---

## 🔪 THE ROAST: Where Your "Hybrid Intelligence" Is Just Confusion

### 1. The Edge Family Architecture: Modular ≠ Independent

**What You Built:**

```python
# app/consumer/edge_families/mean_reversion.py:105-110
if direction == "LONG" and funding_rate < -0.0003:
    base_score += 15  # Reversal bonus
elif direction == "SHORT" and funding_rate > 0.0003:
    base_score += 15
```

**The Roast:**
Congratulations! You've successfully extracted hardcoded magic numbers into modular classes. That's like rearranging deck chairs on the Titanic and calling it "naval architecture reform."

Your "edge families" aren't independent return streams—they're the same heuristic soup you had before, just in separate files. A `-0.0003` funding rate threshold is still a magic number whether it lives in `mean_reversion.py` or `decision_engine.py`.

**The Real Problem:** Funding rates are non-stationary. In a bull market, -0.03% is noise. In a choppy market, it might signal something. By not normalizing this (e.g., Z-scoring against a 72h rolling distribution), you're guaranteed to get run over when regime shifts occur.

**Evidence from Code:**

- `MeanReversion` uses `funding_rate < -0.0003` (line 105)
- No Z-score normalization anywhere in edge families
- Session multipliers hardcoded by UTC hour (lines 127-136)

---

### 2. Score Composer: Apples-to-Oranges Comparison Engine

**What You Built:**

```python
# app/consumer/score_composer.py:159-160
winning = max(valid_results.values(), key=lambda r: r.score)
```

**The Roast:**
A `TrendContinuation` score of 85 is **not mathematically comparable** to a `MeanReversion` score of 85. They have different:

- Win rates
- Payoff ratios
- Holding periods
- Tail risks
- Sensitivity to transaction costs

Picking the "highest score" is like comparing the top speed of a Ferrari to the towing capacity of a truck and choosing the "best" vehicle based on the bigger number. This isn't hybrid intelligence—it's category confusion.

**Missing:**

- No Rolling Information Coefficient (IC) tracking per family
- No Sharpe ratio normalization across families
- No regime-adjusted performance attribution

---

### 3. Sizing Pipeline: Multiplier Soup Theater

**What You Built:**

```python
# app/consumer/sizing_pipeline.py:120-126
base_risk = Decimal("0.10") * Decimal(str(kelly_fraction))
scaled = base_risk * drawdown_mult * conviction_mult * macro_mult * uncertainty_factor * garch_factor
scaled *= profile.size_multiplier * session_mult
scaled = max(Decimal("0.005"), min(Decimal("0.020"), scaled))  # THE SMOKING GUN
```

**The Roast:**
This isn't quantitative finance; this is a slot machine. You calculate a Kelly fraction (already notoriously unstable in fat-tailed crypto markets), then run it through **seven** different heuristic multipliers.

Worse, you hard-cap the final risk at 0.5%-2.0%. If your Kelly and multipliers suggest 5%, you chop it to 2%. If they suggest 0.1%, you bump it to 0.5%. **This means your Kelly calculation is pure decorative theater.** It has zero actual impact on the final output.

You've built a complex Rube Goldberg machine that just spits out a hardcoded risk range. The Kelly fraction, GARCH targeting, and uncertainty adjustments are all cosmetic.

**The Math Doesn't Lie:**

- Kelly is calculated from last 30 trades (unstable sample)
- Then multiplied by 7 different factors
- Then clipped to [0.005, 0.020]
- Result: Kelly contributes nothing meaningful

---

### 4. Side Normalization: Still Leaking After "Fix"

**What You Have:**

```python
# app/core/position_store.py:24-35
def _normalize_side(side: str) -> str:
    if side in ("buy", "Buy", "LONG"):
        return "LONG"
    if side in ("sell", "Sell", "SHORT"):
        return "SHORT"
    logger.warning("position_store: unknown side=%r — defaulting to LONG", side)
    return "LONG"
```

**The Roast:**
You have a normalization function, but the trade analysis shows **four different side representations** in your database:

- `buy` (lowercase): 85 trades, +1.00 PnL ✅
- `sell` (lowercase): 45 trades, +0.24 PnL ✅
- `Buy` (capitalized): 229 trades, -103.40 PnL ❌
- `Sell` (capitalized): 119 trades, -22.90 PnL ❌
- `LONG`: 317 trades, -36.37 PnL
- `SHORT`: 81 trades, -8.21 PnL

The lowercase variants are profitable. The capitalized ones are getting slaughtered. This suggests:

1. Different code paths with different side formats
2. One path is working, the others are bleeding money
3. Your normalization function exists but isn't being called consistently

**The Leak:** Normalization happens at storage time, but different callers are passing different formats. Some call `_normalize_side()`, others don't. The inconsistency is baked into your trade history.

---

### 5. AI Confidence: NULL Across All Trades

**What You Claim:**

```python
# app/alpha/analyst.py:40
ai_confidence: int  # 0-100
```

**What's Actually Happening:**
Trade analysis shows `ai_confidence` column is **NULL for all 876 trades**.

**The Roast:**
You have an entire AI analyst module (`app/alpha/analyst.py`) producing confidence scores, but they're not being recorded in the trades table. This is like building a Formula 1 car and forgetting to install the telemetry sensors.

Without AI confidence data:

- Can't assess if AI filtering improves performance
- Can't backtest confidence thresholds
- Can't optimize the hybrid intelligence loop
- Your "AI-enhanced" claims are untestable hypotheses

**Where It Breaks:**

- `AnalystResult` has `ai_confidence` field
- `live_loop.py` receives `analyst_result.ai_confidence`
- But trade insertion doesn't persist it to the database

---

### 6. Reconciliation/Backfill Trades: Poisoning Your Metrics

**What You Have:**

```python
# app/core/trade_reconciler.py:181, 245, 743
regime="BACKFILL"
regime="RECONCILED"
exit_reason="bybit_sync"
exit_reason="bybit_reconciled"
```

**The Roast:**
69% of your trades (608 of 876) are system-level reconciliation/backfill operations, not strategy signals. These trades:

- Account for **77% of total losses** (-131.79 PnL vs -37.85 for strategy trades)
- Are included in your performance metrics
- Are poisoning your Kelly calculations
- Are corrupting your ELO ratings
- Are making your Sharpe ratio meaningless

**Why This Matters:**
Your sizing pipeline reads the last 30 trades to calculate Kelly. If 20 of those are reconciliation artifacts, your Kelly fraction is garbage. Your entire position sizing logic is based on contaminated data.

---

### 7. Mean Reversion in Trends: The "Moderate Penalty" Delusion

**What You Built:**

```python
# app/consumer/edge_families/mean_reversion.py:116-121
if multi_tf and symbol not in ["BTC/USDT", "ETH/USDT"]:
    macro_penalty = await multi_tf.get_macro_anchor_penalty(direction)
    # For mean reversion, apply moderate penalty (not hard block)
    score *= max(0.7, macro_penalty)
```

**The Roast:**
If the higher-timeframe macro anchor is trending strongly, mean reversion is **dead**. It shouldn't get a "moderate penalty" (a 0.7x multiplier). It should get a **hard block**.

Scaling down a bad idea just means you lose money slower. Mean reversion in strong trends is the #1 cause of quant account blowups. Your code acknowledges this with a penalty but doesn't enforce it.

**Compare to Best Practice:**

- Renaissance Technologies: Hard regime gates
- Two Sigma: Strategy shutdown on regime mismatch
- Your Code: "Let's try it but at 70% size!"

---

### 8. Session Multipliers: Trading the Clock, Not the Market

**What You Built:**

```python
# app/consumer/edge_families/mean_reversion.py:124-137
now_utc = datetime.now(UTC)
hour = now_utc.hour
if 0 <= hour < 7:
    session_mult = 0.7
elif 7 <= hour < 12:
    session_mult = 1.0
elif 12 <= hour < 16:
    session_mult = 1.2  # "London/New York overlap"
elif 16 <= hour < 21:
    session_mult = 1.0
else:
    session_mult = 0.8
```

**The Roast:**
Crypto is 24/7. While traditional equities have open/close bells, crypto liquidity shifts are driven by:

- Global macro events
- Options expiry flows
- Cross-exchange arbitrage
- Weekend gap risks

Boosting conviction because it's "12:00 to 16:00 UTC" is a classic retail backtest overfit. What about:

- Asian open overlap (00:00-02:00 UTC)?
- Low-liquidity weekends where this multiplier gets you slipped to death?
- Macro news drops at random times?

**Better Approach:** Trade the market state (realized vol, order book depth), not the clock.

---

## 🛠 THE REFINEMENT PLAN: From Heuristic Soup to Statistical Rigor

### Phase 1: Data Hygiene (Week 1) — **CRITICAL**

#### 1.1 Fix Side Normalization at Ingestion

**Problem:** Inconsistent side formats corrupting analytics
**Solution:** Enforce normalization at the gateway layer, not storage

```python
# NEW: app/core/market_data_gateway.py
class MarketDataGateway:
    """Single entry point for all market data ingestion."""

    @staticmethod
    def normalize_side(side: str) -> str:
        """Enforce canonical 'LONG'/'SHORT' at ingestion."""
        s = side.strip().upper()
        if s in ("BUY", "LONG"):
            return "LONG"
        if s in ("SELL", "SHORT"):
            return "SHORT"
        raise ValueError(f"Invalid side: {side}")

    async def ingest_trade(self, trade: dict) -> None:
        """All trades must pass through here."""
        trade["side"] = self.normalize_side(trade["side"])
        # ... rest of ingestion
```

**Action Items:**

- [ ] Audit all `create_order` calls to ensure they use gateway
- [ ] Add validation in `PositionManager.execute_order()`
- [ ] Write migration script to fix historical data
- [ ] Add unit tests for all side variants

#### 1.2 Separate System vs Strategy Trades

**Problem:** Reconciliation trades poisoning metrics
**Solution:** Add `trade_type` enum and filter in analytics

```python
# NEW: app/core/trade_models.py
from enum import Enum

class TradeType(Enum):
    STRATEGY = "strategy"
    RECONCILIATION = "reconciliation"
    BACKFILL = "backfill"

@dataclass
class TradeRecord:
    trade_type: TradeType = TradeType.STRATEGY  # NEW FIELD
    # ... existing fields
```

**Action Items:**

- [ ] Update database schema to add `trade_type` column
- [ ] Modify `TradeReconciler` to mark trades as `RECONCILIATION`/`BACKFILL`
- [ ] Update `TradeAnalyzer` to exclude non-STRATEGY trades from metrics
- [ ] Create separate audit table for system trades

#### 1.3 Persist AI Confidence Scores

**Problem:** `ai_confidence` is NULL everywhere
**Solution:** Trace the data flow and plug the leak

```python
# FIX: app/consumer/live_loop.py ~line 400
await trade_store.insert_trade(
    # ... existing fields
    ai_confidence=analyst_result.ai_confidence,  # ADD THIS
    ai_model_used=analyst_result.model_used,     # ADD THIS
)
```

**Action Items:**

- [ ] Trace `AnalystResult` from generation to storage
- [ ] Add `ai_confidence` to all trade insertions
- [ ] Verify with query: `SELECT COUNT(*) FROM trades WHERE ai_confidence IS NOT NULL`
- [ ] Build dashboard showing AI confidence vs realized PnL

---

### Phase 2: Signal Normalization (Week 2-3)

#### 2.1 Replace Magic Numbers with Z-Scores

**Problem:** Hardcoded thresholds fail in non-stationary markets
**Solution:** Dynamic statistical bounds

```python
# NEW: app/core/stat_utils.py
import numpy as np
from typing import List

class StatUtils:
    @staticmethod
    def z_score(value: float, series: List[float]) -> float:
        """Calculate Z-score relative to rolling distribution."""
        if len(series) < 10:
            return 0.0  # Insufficient data
        mean = np.mean(series)
        std = np.std(series)
        if std == 0:
            return 0.0
        return (value - mean) / std

    @staticmethod
    def percentile_rank(value: float, series: List[float]) -> float:
        """Calculate percentile rank."""
        count_below = sum(1 for x in series if x < value)
        return count_below / len(series)
```

**Refactor Example:**

```python
# BEFORE: app/consumer/edge_families/mean_reversion.py:105
if direction == "LONG" and funding_rate < -0.0003:
    base_score += 15

# AFTER
funding_history = await self._get_funding_history(symbol, window_h=72)
funding_z = StatUtils.z_score(funding_rate, funding_history)
if direction == "LONG" and funding_z < -2.0:  # 2 std dev extreme
    base_score += 15
```

**Action Items:**

- [ ] Create `StatUtils` module
- [ ] Replace all magic numbers in edge families:
  - Funding rate threshold → Z-score < -2.0
  - RSI levels → Percentile ranks
  - BB edges → Dynamic std dev multiples
- [ ] Backtest old vs new thresholds

#### 2.2 Implement Rolling Information Coefficient (IC)

**Problem:** ScoreComposer picks highest score, not best performer
**Solution:** Track predictive power per family

```python
# NEW: app/learning/ic_tracker.py
from dataclasses import dataclass
from typing import Deque
import numpy as np
from scipy.stats import pearsonr

@dataclass
class ICRecord:
    predicted_score: float
    actual_return: float
    timestamp: datetime

class ICTracker:
    """Track Rolling Information Coefficient per edge family."""

    def __init__(self, window: int = 20):
        self.window = window
        self.history: Deque[ICRecord] = Deque(maxlen=window)

    def add_observation(self, predicted_score: float, actual_return: float) -> None:
        """Record prediction vs outcome."""
        self.history.append(ICRecord(
            predicted_score=predicted_score,
            actual_return=actual_return,
            timestamp=datetime.now(UTC)
        ))

    def get_ic(self) -> float:
        """Calculate Rolling IC (Pearson correlation)."""
        if len(self.history) < 10:
            return 0.0  # Insufficient data

        predictions = [r.predicted_score for r in self.history]
        returns = [r.actual_return for r in self.history]

        ic, _ = pearsonr(predictions, returns)
        return ic if not np.isnan(ic) else 0.0

    def is_tradable(self, threshold: float = 0.05) -> bool:
        """Check if IC is above minimum threshold."""
        return self.get_ic() > threshold
```

**Integration:**

```python
# UPDATE: app/consumer/score_composer.py
async def compose(self, ...) -> ComposedScore:
    # ... evaluate families ...

    # NEW: Filter families by IC
    tradable_families = {
        name: result for name, result in family_results.items()
        if self._ic_trackers[name].is_tradable()
    }

    if not tradable_families:
        return self._reject_all("no_family_with_positive_ic")

    # Pick winner from tradable families only
    winning = max(tradable_families.values(), key=lambda r: r.score)
```

**Action Items:**

- [ ] Create `ICTracker` class
- [ ] Add IC tracking to each `EdgeFamily`
- [ ] Store IC in Redis for monitoring
- [ ] Block families with IC < 0.05
- [ ] Dashboard showing IC over time per family

#### 2.3 Enforce Hard Regime Gates

**Problem:** "Moderate penalties" allow bad strategies to bleed
**Solution:** Binary regime filters

```python
# UPDATE: app/consumer/edge_families/base.py
class EdgeFamily(ABC):
    @abstractmethod
    def supported_regimes(self) -> list[MarketRegime]:
        """Which regimes this family can trade."""
        pass

    @abstractmethod
    def blocked_regimes(self) -> list[MarketRegime]:
        """Which regimes explicitly block this family."""
        return []  # Default: no explicit blocks

    def is_active(self, regime: MarketRegime) -> bool:
        """Check if family is active for regime."""
        if regime in self.blocked_regimes():
            return False  # HARD BLOCK
        return regime in self.supported_regimes()
```

**Example Implementation:**

```python
# UPDATE: app/consumer/edge_families/mean_reversion.py
class MeanReversion(EdgeFamily):
    @property
    def supported_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGE]

    @property
    def blocked_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR]  # HARD BLOCK
```

**Action Items:**

- [ ] Add `blocked_regimes()` to base class
- [ ] Implement for all edge families
- [ ] Remove "moderate penalty" logic from mean reversion
- [ ] Test regime transitions

---

### Phase 3: Position Sizing Reconstruction (Week 4-5)

#### 3.1 Dismantle Multiplier Soup

**Problem:** 7-layer sizing cake renders Kelly useless
**Solution:** Single volatility targeting model

```python
# NEW: app/risk/volatility_target_sizer.py
from decimal import Decimal
import numpy as np

class VolatilityTargetSizer:
    """Institutional-grade volatility targeting."""

    def __init__(
        self,
        target_annual_vol: float = 0.15,  # 15% annualized
        lookback_days: int = 30,
    ):
        self.target_annual_vol = target_annual_vol
        self.lookback_days = lookback_days

    def calculate_position_size(
        self,
        account_equity: Decimal,
        asset_price: Decimal,
        forecasted_vol: float,  # Annualized
        drawdown_scaler: float = 1.0,
    ) -> Decimal:
        """
        Calculate position size using volatility targeting.

        Formula:
            Position Value = (Equity × Target Vol) / Asset Vol
            Final Position = Position Value × Drawdown Scaler
        """
        # Convert annualized vol to per-period (assuming hourly)
        target_vol_period = self.target_annual_vol / np.sqrt(252 * 24)
        asset_vol_period = forecasted_vol / np.sqrt(252 * 24)

        if asset_vol_period == 0:
            return Decimal("0")

        # Core volatility targeting formula
        target_position_value = (account_equity * Decimal(str(target_vol_period))) / Decimal(str(asset_vol_period))

        # Apply drawdown scaler (anti-martingale)
        adjusted_position_value = target_position_value * Decimal(str(drawdown_scaler))

        # Convert to asset units
        position_size = adjusted_position_value / asset_price

        return position_size
```

**Action Items:**

- [ ] Create `VolatilityTargetSizer`
- [ ] Deprecate `SizingPipeline.calculate()` multipliers
- [ ] Keep only `drawdown_scaler` from old pipeline
- [ ] Integrate GARCH forecast as `forecasted_vol`
- [ ] Backtest vs old sizing

#### 3.2 Add Execution Reality Check

**Problem:** No slippage modeling, order book awareness
**Solution:** Pre-trade liquidity check

```python
# NEW: app/execution/liquidity_checker.py
class LiquidityChecker:
    """Check if order size exceeds safe liquidity thresholds."""

    def __init__(self, exchange_client):
        self.exchange = exchange_client
        self.max_book_pct = 0.05  # Max 5% of 1% book depth

    async def check_liquidity(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
    ) -> tuple[bool, str]:
        """
        Check if order can be filled without excessive slippage.

        Returns:
            (can_execute, reason)
        """
        orderbook = await self.exchange.get_orderbook(symbol, limit=20)

        # Calculate 1% depth
        one_pct_price = price * Decimal("0.01")
        depth_qty = Decimal("0")

        for level in orderbook["bids" if side == "BUY" else "asks"]:
            level_price = Decimal(str(level["price"]))
            level_qty = Decimal(str(level["qty"]))

            if abs(level_price - price) <= one_pct_price:
                depth_qty += level_qty
            else:
                break

        if depth_qty == 0:
            return False, "insufficient_liquidity"

        # Check if order exceeds threshold
        if quantity > depth_qty * Decimal(str(self.max_book_pct)):
            return False, f"order_exceeds_{int(self.max_book_pct*100)}pct_of_book"

        return True, "ok"
```

**Action Items:**

- [ ] Create `LiquidityChecker`
- [ ] Integrate into `DecisionEngineV2` before signal emission
- [ ] Add TWAP/VWAP slicing for large orders
- [ ] Model dynamic slippage in backtests

---

### Phase 4: Hybrid Intelligence Integration (Week 6-7)

#### 4.1 Close the AI Confidence Loop

**Problem:** AI predictions not recorded, can't learn
**Solution:** Full prediction → outcome tracking

```python
# NEW: app/learning/ai_feedback_loop.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class AIPrediction:
    symbol: str
    direction: str
    predicted_direction: str
    confidence: float  # 0-1
    model_version: str
    features_hash: str
    timestamp: datetime

@dataclass
class AIOutcome:
    prediction_id: str
    actual_return: float
    realized_direction: str
    holding_period_h: int
    pnl_pct: float

class AIFeedbackLoop:
    """Track AI predictions vs outcomes for continuous learning."""

    def __init__(self, redis_client, db_connection):
        self.redis = redis_client
        self.db = db_connection

    async def record_prediction(self, prediction: AIPrediction) -> str:
        """Store prediction and return ID for later matching."""
        prediction_id = str(uuid.uuid4())
        await self.redis.hset(
            f"ai:prediction:{prediction_id}",
            mapping={
                "symbol": prediction.symbol,
                "direction": prediction.direction,
                "confidence": prediction.confidence,
                "model_version": prediction.model_version,
                "timestamp": prediction.timestamp.isoformat(),
            }
        )
        return prediction_id

    async def record_outcome(self, outcome: AIOutcome) -> None:
        """Match outcome to prediction and store for training."""
        prediction_data = await self.redis.hgetall(f"ai:prediction:{outcome.prediction_id}")

        if not prediction_data:
            logger.error(f"Prediction {outcome.prediction_id} not found")
            return

        # Store matched pair for model retraining
        await self.db.execute("""
            INSERT INTO ai_training_data
            (prediction_id, symbol, predicted_dir, confidence,
             actual_return, pnl_pct, model_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            outcome.prediction_id,
            prediction_data["symbol"],
            prediction_data["direction"],
            prediction_data["confidence"],
            outcome.actual_return,
            outcome.pnl_pct,
            prediction_data["model_version"],
        ))

    async def get_model_performance(
        self,
        model_version: str,
        window_days: int = 30,
    ) -> dict:
        """Calculate model performance metrics."""
        # Query ai_training_data for recent predictions
        # Return: win_rate, avg_pnl, sharpe, calibration_curve
        pass
```

**Action Items:**

- [ ] Create `AIFeedbackLoop` class
- [ ] Record prediction at signal generation
- [ ] Record outcome at trade close
- [ ] Build weekly retraining pipeline
- [ ] Dashboard: AI confidence deciles vs realized returns

#### 4.2 Implement Confidence-Weighted Scoring

**Problem:** AI confidence not used in decision making
**Solution:** Use AI as gating/weighting layer

```python
# UPDATE: app/consumer/decision_engine_v2.py
async def evaluate_candidate(self, ...) -> TradeSignal | None:
    # ... existing logic ...

    # NEW: AI confidence weighting
    analyst_result = await self.analyst.predict(symbol, features)

    if analyst_result.direction == "FLAT":
        return None  # AI vetoes trade

    # Weight score by AI confidence
    ai_weight = analyst_result.confidence / 100.0  # Normalize to 0-1
    weighted_score = composed_score.total_score * ai_weight

    # Only proceed if weighted score passes threshold
    if weighted_score < self.gate_threshold:
        return None
```

**Action Items:**

- [ ] Add AI veto power for low-confidence predictions
- [ ] Weight edge family scores by AI confidence
- [ ] A/B test AI-weighted vs unweighted scoring
- [ ] Track incremental value of AI layer

---

### Phase 5: Attribution & Monitoring (Week 8)

#### 5.1 Build Per-Family Attribution Reports

**Problem:** Can't tell which edge families actually work
**Solution:** Detailed performance attribution

```sql
-- NEW: app/analytics/queries/family_attribution.sql
SELECT
    edge_family,
    regime,
    direction,
    COUNT(*) as trade_count,
    SUM(pnl_usd) as total_pnl,
    AVG(pnl_usd) as avg_pnl,
    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
    AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
    AVG(CASE WHEN pnl_usd < 0 THEN pnl_usd END) as avg_loss,
    (AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) *
     (SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT())) +
    (AVG(CASE WHEN pnl_usd < 0 THEN pnl_usd END) *
     (SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) * 1.0 / COUNT())) as expected_value
FROM trades
WHERE trade_type = 'STRATEGY'
  AND edge_family IS NOT NULL
GROUP BY edge_family, regime, direction
ORDER BY expected_value DESC;
```

**Action Items:**

- [ ] Add `edge_family` column to trades table
- [ ] Populate on trade insertion
- [ ] Build daily attribution report
- [ ] Alert on families with negative EV over 20 trades

#### 5.2 Create Hybrid Intelligence Dashboard

**Metrics to Track:**

1. **Edge Family Performance**
   - Rolling IC per family
   - Win rate by regime
   - Expected value per setup type

2. **AI Layer Effectiveness**
   - AI confidence distribution
   - Confidence vs realized accuracy
   - Incremental EV from AI filtering

3. **Sizing Quality**
   - Volatility forecast accuracy
   - Position size vs optimal Kelly
   - Drawdown scaler effectiveness

4. **Data Hygiene**
   - % trades with normalized sides
   - % trades with AI confidence recorded
   - System vs strategy trade ratio

---

## 📋 IMPLEMENTATION CHECKLIST

### Week 1: Data Hygiene (P0 — Do This First)

- [ ] Fix side normalization at ingestion gateway
- [ ] Add `trade_type` enum to schema
- [ ] Migrate historical data to canonical format
- [ ] Exclude reconciliation trades from metrics
- [ ] Persist AI confidence to database
- [ ] Verify with analytics queries

### Week 2-3: Signal Normalization (P1)

- [ ] Create `StatUtils` module
- [ ] Replace magic numbers with Z-scores
- [ ] Implement `ICTracker` per edge family
- [ ] Add IC-based family filtering
- [ ] Enforce hard regime gates
- [ ] Remove "moderate penalty" logic

### Week 4-5: Sizing Reconstruction (P1)

- [ ] Build `VolatilityTargetSizer`
- [ ] Deprecate multiplier soup
- [ ] Integrate GARCH forecasts
- [ ] Add `LiquidityChecker`
- [ ] Model slippage in backtests
- [ ] A/B test old vs new sizing

### Week 6-7: AI Integration (P2)

- [ ] Create `AIFeedbackLoop`
- [ ] Record predictions and outcomes
- [ ] Build retraining pipeline
- [ ] Implement AI veto logic
- [ ] Confidence-weighted scoring
- [ ] Measure incremental AI value

### Week 8: Attribution (P2)

- [ ] Add `edge_family` to trade records
- [ ] Build attribution SQL queries
- [ ] Create Grafana dashboard
- [ ] Set up alerts for negative EV families
- [ ] Document findings

---

## 🎯 SUCCESS METRICS

### Data Quality

- [ ] 100% of trades have normalized sides (`LONG`/`SHORT`)
- [ ] 100% of trades have `ai_confidence` recorded
- [ ] 0% of strategy metrics include reconciliation trades
- [ ] Side representation bug fixed: no more `buy`/`Buy` variance

### Signal Quality

- [ ] All edge families have Rolling IC > 0.05
- [ ] Zero magic numbers in production code
- [ ] Hard regime gates enforced (no MR in trends)
- [ ] Z-score normalization for all thresholds

### Sizing Quality

- [ ] Position sizes inversely correlate with volatility
- [ ] No hard caps unrelated to volatility
- [ ] Slippage modeled in all backtests
- [ ] Drawdown scaler smooth, not step-function

### AI Integration

- [ ] AI confidence distribution: mean 0.4-0.7, std 0.15
- [ ] High-confidence (>0.7) trades have 15%+ higher win rate
- [ ] Retraining pipeline runs weekly
- [ ] Measurable EV improvement from AI filtering

---

## FINAL VERDICT

You've built a **Ferrari chassis** (clean architecture, modular design, solid CI/CD) with a **lawnmower engine** (heuristic multipliers, magic numbers, unnormalized scores).

The path forward is clear:

1. **Strip out the decorative math** (Kelly theater, multiplier soup)
2. **Normalize signals to true statistical edges** (Z-scores, IC tracking)
3. **Size by volatility, not committee** (single robust model)
4. **Close the AI feedback loop** (record, measure, learn)
5. **Attribute performance honestly** (per-family, per-regime, per-setup)

Do this, and you might just have a bot that survives the crypto meat grinder. Until then, you're running a sophisticated backtest generator, not a trading system.

**Now go refactor. I want to see Z-scores, not magic numbers.**

---

## APPENDIX: Key Files to Modify

| File | Priority | Changes Required |
| ------ | ---------- | ------------------ |
| `app/core/position_store.py` | P0 | Enforce normalization at write, not read |
| `app/core/trade_reconciler.py` | P0 | Add `trade_type` field |
| `app/consumer/live_loop.py` | P0 | Persist `ai_confidence` |
| `app/consumer/edge_families/*.py` | P1 | Replace magic numbers with Z-scores |
| `app/consumer/score_composer.py` | P1 | Add IC-based filtering |
| `app/consumer/sizing_pipeline.py` | P1 | Replace with volatility targeting |
| `app/learning/ic_tracker.py` | P1 | NEW FILE |
| `app/learning/ai_feedback_loop.py` | P2 | NEW FILE |
| `app/risk/volatility_target_sizer.py` | P1 | NEW FILE |
| `app/execution/liquidity_checker.py` | P1 | NEW FILE |
