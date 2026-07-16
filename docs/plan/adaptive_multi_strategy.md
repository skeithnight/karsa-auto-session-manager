Designing an **Adaptive Multi-Strategy Bot** requires shifting your architecture from a single, linear pipeline into a **hub-and-spoke model**. The "Hub" is the Regime Classifier, and the "Spokes" are the distinct strategies (Trend, Range, Micro-Scalp).

Here is the comprehensive architectural blueprint to upgrade **`karsa-auto-session-manager` (ASM)** into an Adaptive Multi-Strategy Bot, mapping directly to its existing "7 Keys" structure.

---

### 🏗️ The Architecture: Hub-and-Spoke Design

Instead of one Alpha Bridge feeding one Risk Gate, we will implement a **Strategy Router** that sits between the Regime Classifier and the Risk Gate.

```text
[Global Data Engine] -> [Regime Classifier (The Hub)] 
                                |
                        [Strategy Router]
                       /        |        \
            [Trend Strategy] [Range Strategy] [Chop Strategy]
                       \        |        /
                   [Adaptive Confidence Engine]
                                |
                     [Dynamic Risk Gate] -> [Bybit Executor]
```

---

### 🧠 Phase 1: The Regime Classifier (The Hub)

*Location: `app/alpha/regime_classifier.py`*

We need a mathematically robust classifier that doesn't just look at direction, but at **volatility and persistence**.

```python
import numpy as np
from enum import Enum

class MarketRegime(Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGE = "RANGE"
    CHOP = "CHOP"

class RegimeClassifier:
    def __init__(self, lookback_period=100):
        self.lookback = lookback_period

    def classify(self, candles: list, orderbook_delta: float) -> MarketRegime:
        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        
        # 1. Calculate Core Metrics
        adx = self._calculate_adx(highs, lows, closes)
        hurst = self._calculate_hurst(closes)
        atr_percentile = self._calculate_atr_percentile(highs, lows, closes)
        bb_width = self._calculate_bollinger_width(closes)
        
        # 2. Classification Logic
        # CHOP: High volatility, no trend, tight Bollinger Bands squeezing then expanding erratically
        if atr_percentile > 80 and adx < 20:
            return MarketRegime.CHOP
            
        # TREND: Strong directional movement
        if adx >= 25:
            # Use linear regression slope or simple price vs SMA to determine direction
            if closes[-1] > np.mean(closes[-20:]):
                return MarketRegime.TREND_BULL
            else:
                return MarketRegime.TREND_BEAR
                
        # RANGE: Low volatility, mean-reverting
        if adx < 20 and hurst < 0.45:
            return MarketRegime.RANGE
            
        # Fallback
        return MarketRegime.RANGE
```

---

### 🧭 Phase 2: The Strategy Router & Adaptive Confidence

*Location: `app/alpha/strategy_router.py`*

This is where the magic happens. The router takes the regime and applies a **completely different set of rules** to calculate signal confidence.

```python
class StrategyRouter:
    def __init__(self, global_data_engine):
        self.global_data = global_data_engine # ASM's multi-exchange data

    def evaluate_signal(self, symbol: str, candles: list, regime: MarketRegime, direction: str) -> float:
        """Returns a confidence score from 0 to 100."""
        
        if regime in [MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR]:
            return self._score_trend_strategy(symbol, candles, direction)
            
        elif regime == MarketRegime.RANGE:
            return self._score_range_strategy(symbol, candles, direction)
            
        elif regime == MarketRegime.CHOP:
            return self._score_chop_strategy(symbol, candles, direction)
            
        return 0.0

    def _score_trend_strategy(self, symbol, candles, direction):
        score = 0.0
        # 1. Momentum: Price breaking 20-high/low
        if self._is_breakout(candles, direction): score += 30
        # 2. Volume: Current volume > 1.5x 20 SMA
        if self._is_volume_surge(candles): score += 30
        # 3. GLOBAL FAKEOUT DETECTOR (ASM's Secret Weapon)
        # If Bybit breaks out, but Binance/OKX haven't, it's a fakeout.
        if self.global_data.is_global_sync(symbol, direction): score += 40
        return score

    def _score_range_strategy(self, symbol, candles, direction):
        score = 0.0
        # 1. Edge Touch: Price pierced Bollinger Band (2.5 StdDev)
        if self._is_bb_extreme(candles, direction): score += 40
        # 2. Wick Rejection: Candle closed back inside the range
        if self._is_wick_rejection(candles, direction): score += 40
        # 3. Exhaustion: RSI > 75 (for shorts) or < 25 (for longs)
        if self._is_rsi_extreme(candles, direction): score += 20
        return score

    def _score_chop_strategy(self, symbol, candles, direction):
        score = 0.0
        # 1. Liquidity Sweep: Massive order book delta suddenly reversing
        if self._is_liquidity_sweep(symbol, direction): score += 50
        # 2. Funding Extremes: Funding rate is highly skewed against the direction
        if self._is_funding_extreme(symbol, direction): score += 50
        return score
```

---

### 🛡️ Phase 3: Dynamic Risk & Execution Profiles

*Location: `app/risk/dynamic_risk_gate.py`*

A multi-strategy bot fails if it uses the same Stop Loss for a trend and a range. We must define **Regime-Specific Risk Profiles**.

```python
from dataclasses import dataclass

@dataclass
class RiskProfile:
    size_multiplier: float
    take_profit_type: str   # 'TRAILING', 'FIXED', 'SCALP'
    stop_loss_type: str     # 'WIDE', 'TIGHT', 'MICRO'
    max_hold_time_mins: int
    use_post_only: bool     # Crucial for fee management

class DynamicRiskGate:
    def get_profile(self, regime: MarketRegime) -> RiskProfile:
        if regime in [MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR]:
            return RiskProfile(
                size_multiplier=1.0,
                take_profit_type='TRAILING',
                stop_loss_type='WIDE',       # Below recent swing low
                max_hold_time_mins=1440,     # 24 hours
                use_post_only=False          # Allow market orders for strong breakouts
            )
            
        elif regime == MarketRegime.RANGE:
            return RiskProfile(
                size_multiplier=0.7,         # Reduce size in chop
                take_profit_type='FIXED',    # Target opposite edge of range
                stop_loss_type='TIGHT',      # Just outside the BB/Range edge
                max_hold_time_mins=240,      # 4 hours max
                use_post_only=True           # STRICT Maker fees only
            )
            
        elif regime == MarketRegime.CHOP:
            return RiskProfile(
                size_multiplier=0.3,         # Micro size
                take_profit_type='SCALP',    # Quick 1:1 R:R
                stop_loss_type='MICRO',      # Invalidated immediately if it moves
                max_hold_time_mins=30,       # Get in, get out
                use_post_only=True           # STRICT Maker fees only
            )
```

---

### ⚙️ Phase 4: Execution Adaptation

*Location: `app/execution/bybit_executor.py`*

The executor must respect the `RiskProfile` passed to it.

```python
async def execute_entry(self, signal, risk_profile: RiskProfile):
    # 1. Apply dynamic position sizing
    base_size = self.calculate_base_size(signal.confidence)
    final_size = base_size * risk_profile.size_multiplier
    
    # 2. Enforce Minimum Notional (Fixes the "Dust Trade" leak)
    if (final_size * signal.price) < 50.0: 
        self.logger.info(f"Skipped {signal.symbol}: Size below $50 logical minimum.")
        return None

    # 3. Route order type based on profile
    if risk_profile.use_post_only:
        # Strict Post-Only for Range/Chop to guarantee Maker fees
        return await self.place_post_only_limit(signal.symbol, final_size, signal.price)
    else:
        # Trend strategy can use aggressive limits or market if confidence > 85
        if signal.confidence > 85:
            return await self.place_market_order(signal.symbol, final_size)
        else:
            return await self.place_aggressive_limit(signal.symbol, final_size, signal.price)
```

---

### 📊 Phase 5: Telemetry & State Tracking

*Location: `app/core/state.py` & `app/watchdog/`*

Because you are now running 3 different strategies, you need to know **which strategy is making money and which is losing**.

1. **Tag the State:** When saving a trade to PostgreSQL/Redis, include the `regime` and `strategy_type` (e.g., `strategy: "RANGE_FADE"`).
2. **Grafana Dashboards:** Create a new panel in Prometheus/Grafana that splits your Win Rate and PnL by Regime.
    * *Expected Outcome:* You will likely see that `TREND` has a lower win rate (40%) but massive R:R (1:3), while `RANGE` has a high win rate (70%) but small R:R (1:1). This data is crucial for future tuning.

---

### 🚀 Implementation Roadmap (How to deploy safely)

Do not push this to production all at once. Follow this exact sequence:

1. **Week 1: The Shadow Router (Logging Only)**
    * Implement the `RegimeClassifier` and `StrategyRouter`.
    * *Do not change execution.* Let the bot trade as it normally does, but log what the Router *would have done*.
    * *Goal:* Verify the Regime Classifier accurately matches your human intuition of the market.
2. **Week 2: The Risk Profiles (Paper Trading)**
    * Enable the `DynamicRiskGate`.
    * Run on **Bybit Testnet**. Watch how the bot tightens stops in `RANGE` and widens them in `TREND`.
    * *Goal:* Ensure the dynamic stops aren't getting hunted by normal market noise.
3. **Week 3: Live Execution (Fractional Size)**
    * Deploy to Live. Set a global `MAX_PORTFOLIO_RISK` to 25% of your normal size.
    * *Goal:* Test the "Fakeout Detector" and Post-Only execution in live market conditions without risking significant capital.
4. **Week 4: Full Deployment**
    * Scale back to 100% size. Monitor the Grafana dashboards to ensure the `CHOP` strategy isn't bleeding money. If it is, lower its `size_multiplier` to `0.0` (effectively turning it back into a filter).

### 💡 The Ultimate Advantage

By building this, you are no longer fighting the market.

* When Bitcoin is ripping to all-time highs (`TREND_BULL`), your bot acts like a **momentum beast**, riding the wave with wide stops.
* When the market gets bored and sideways for 3 days (`RANGE`), your bot seamlessly switches to a **mean-reversion scalper**, quietly picking up pennies at the edges of the range while other bots get chopped to pieces.
* When a flash crash or news event causes chaos (`CHOP`), your bot shrinks to a **micro-size**, only taking hyper-quick liquidity sweeps, protecting your capital.

This is how institutional algorithms operate. You are now designing ASM to do the same.
