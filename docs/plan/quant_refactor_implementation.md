# Quant Trader Persona Refactor — Implementation Plan

**Branch:** `feat/quant-trader-persona-refactor`
**Date:** 2026-07-28
**Goal:** Decompose the monolithic `DecisionEngine` (1447 lines) and `live_loop.py` (1895 lines) into modular, family-aware, reusable components.

---

## Executive Summary

The current `DecisionEngine.evaluate()` method is a 700+ line function that:
- Mixes 13+ additive scoring overlays (bonus/penalty stack)
- Treats all edge families (trend, carry, breakout, mean-reversion) identically
- Has sizing logic interleaved with signal evaluation
- Makes the system hard to calibrate, backtest, and attribute alpha

This refactor decomposes the monolith into:
1. **Edge Families** — Each strategy type is a standalone evaluator with its own filters
2. **Score Composers** — Compose family scores without mutual awareness
3. **Signal Builders** — Build TradeSignal from evaluation results
4. **Sizing Pipeline** — Separate sizing from evaluation
5. **Background Loops** — Extract loops from live_loop.py into standalone modules

---

## Phase 1: Create Edge Family Base Classes

**Files to create:**
- `app/consumer/edge_families/__init__.py`
- `app/consumer/edge_families/base.py`
- `app/consumer/edge_families/trend.py`
- `app/consumer/edge_families/mean_reversion.py`
- `app/consumer/edge_families/carry.py`
- `app/consumer/edge_families/liquidation_squeeze.py`
- `app/consumer/edge_families/event_breakout.py`

### Base Class: `app/consumer/edge_families/base.py`

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.alpha.regime_classifier import MarketRegime
from app.core.decision_context import DecisionContext
from app.core.market_snapshot import MarketSnapshot


@dataclass(frozen=True)
class EdgeFamilyResult:
    """Result from an edge family evaluation."""
    family_name: str
    score: float
    confidence: float
    regime_aligned: bool
    filters_passed: dict[str, bool]
    metadata: dict[str, Any] = field(default_factory=dict)
    reject_reason: str | None = None


class EdgeFamily(ABC):
    """Base class for all edge families.

    Each family encapsulates:
    - Its own entry logic
    - Its own regime requirements
    - Its own filter set
    - Its own expected holding period
    - Its own stop/target behavior
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique family name."""
        ...

    @property
    @abstractmethod
    def supported_regimes(self) -> list[MarketRegime]:
        """Regimes where this family is active."""
        ...

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        direction: str,
        regime: MarketRegime,
        features: Any,
        snapshot: MarketSnapshot,
        context: DecisionContext | None = None,
        redis_client: Any = None,
    ) -> EdgeFamilyResult:
        """Evaluate this family for the given symbol/direction."""
        ...

    def is_active(self, regime: MarketRegime) -> bool:
        """Check if this family is active for the given regime."""
        return regime in self.supported_regimes
```

---

## Phase 2: Implement Individual Edge Families

### 2a. Trend Continuation — `app/consumer/edge_families/trend.py`

**Responsibilities:**
- Regime filter: TREND_BULL/BEAR, HYPER_BULL/BEAR
- MTF alignment (4H EMA)
- Macro anchor block
- Session volatility filter
- Momentum exemption logic

**Score components:**
- Base strategy score from StrategyRouter
- ELO adjustment (strategy confidence)
- MTF penalty/bonus
- Session multiplier

**Filters:**
- Macro momentum hard block (for non-BTC/ETH)
- Multi-timeframe trend alignment

### 2b. Mean Reversion — `app/consumer/edge_families/mean_reversion.py`

**Responsibilities:**
- Regime filter: RANGE
- RSI/Bollinger Band mean reversion signals
- Sector rotation alignment
- Funding rate extremes (reversal signal)

**Score components:**
- Base strategy score
- RSI divergence bonus
- BB width contraction bonus
- Funding rate reversal bonus

**Filters:**
- Sector rotation alignment (not hard block)
- Moderate macro penalty (not hard block)

### 2c. Carry / Positioning Dislocation — `app/consumer/edge_families/carry.py`

**Responsibilities:**
- Funding rate carry (negative funding → LONG bonus)
- Funding term structure (squeeze imminent signals)
- OI divergence signals

**Score components:**
- Carry bonus from StrategyRouter
- Term structure bonus (+25 for squeeze)
- OI divergence bonus

**Filters:**
- Funding rate extremes (block opposite direction)
- Macro-aware but not macro-dominated

### 2d. Liquidation / Squeeze — `app/consumer/edge_families/liquidation_squeeze.py`

**Responsibilities:**
- Liquidation heatmap signal
- Cross-asset momentum alignment
- OI delta patterns

**Score components:**
- Liquidation heatmap bonus
- Cross-asset momentum bonus
- Squeeze momentum bonus

**Filters:**
- Allow explicit exemption path (squeeze can begin while anchors disagree)
- Cross-asset alignment (not hard block)

### 2e. Event Breakout — `app/consumer/edge_families/event_breakout.py`

**Responsibilities:**
- HMM regime prediction (breakout imminent)
- Token unlock calendar (sell pressure)
- Volatility floor check

**Score components:**
- HMM breakout bonus (scaled by confidence)
- Token unlock penalty
- Volatility floor penalty

**Filters:**
- HMM confidence threshold
- Token unlock window check

---

## Phase 3: Create Score Composer

**File:** `app/consumer/score_composer.py`

The ScoreComposer evaluates all active families independently and picks the winning family.

```python
@dataclass
class ComposedScore:
    """Final composed score from all edge families."""
    symbol: str
    direction: str
    regime: MarketRegime
    total_score: float
    family_scores: dict[str, EdgeFamilyResult]
    winning_family: str | None
    confidence: float
    regime_aligned: bool
    filters_passed: dict[str, bool]


class ScoreComposer:
    """Composes scores from multiple edge families."""

    def __init__(self, families: list[EdgeFamily] | None = None):
        self._families = families or []

    async def compose(
        self, symbol, direction, regime, features, snapshot, context, redis
    ) -> ComposedScore:
        """Evaluate all active families and compose final score."""
        ...
```

---

## Phase 4: Extract Sizing Pipeline

**File:** `app/consumer/sizing_pipeline.py`

Extract all sizing logic from `DecisionEngine._build_signal()` into a standalone pipeline:

```python
@dataclass
class SizingResult:
    """Result from the sizing pipeline."""
    amount: Decimal
    risk_pct: Decimal
    kelly_fraction: float
    drawdown_mult: float
    conviction_mult: float
    macro_mult: float
    session_mult: float
    uncertainty_factor: float
    garch_factor: float


class SizingPipeline:
    """Standalone sizing pipeline."""

    async def calculate(
        self, symbol, wallet_balance, entry_price, sl_price, score, profile, session_mult
    ) -> SizingResult:
        """Run full sizing pipeline."""
        ...
```

---

## Phase 5: Refactor DecisionEngine

**File:** `app/consumer/decision_engine.py` (refactored)

The refactored `DecisionEngine` becomes a thin orchestrator:

```python
class DecisionEngine:
    """Orchestrator for the decision pipeline."""

    def __init__(self, analyzer, router, risk_gate, ...):
        self._score_composer = ScoreComposer(families=[
            TrendContinuation(),
            MeanReversion(),
            CarryDislocation(),
            LiquidationSqueeze(),
            EventBreakout(),
        ])
        self._sizing = SizingPipeline(trade_memory=..., redis_client=...)

    async def evaluate(self, symbol, candles, ...) -> TradeSignal | None:
        """Run the full decision pipeline."""
        # ... pre-checks
        # ... feature extraction
        # ... regime classification

        for direction in directions:
            composed = await self._score_composer.compose(...)
            if composed.total_score < effective_gate:
                continue
            sizing = await self._sizing.calculate(...)
            signal = self._build_signal(...)
            # Track best EV
        return best_signal
```

---

## Phase 6: Extract Background Loops

**Files to create:**
- `app/consumer/loops/__init__.py`
- `app/consumer/loops/wallet_metrics.py`
- `app/consumer/loops/ranking_refresh.py`
- `app/consumer/loops/elo_refresh.py`
- `app/consumer/loops/gate_calibration.py`
- `app/consumer/loops/vol_surface.py`
- `app/consumer/loops/hmm_classification.py`
- `app/consumer/loops/garch_forecast.py`
- `app/consumer/loops/position_exit.py`

Each loop becomes a standalone async function with clear signature, configurable interval, and proper error handling.

---

## Phase 7: Refactor live_loop.py

**File:** `app/consumer/live_loop.py` (refactored)

After extracting loops, `live_loop.py` becomes a thin orchestrator (~300 lines, down from 1895).

---

## Implementation Order

| Step | Description | Files | Est. Lines |
|------|-------------|-------|------------|
| 1 | Create edge_families base | `edge_families/base.py` | ~80 |
| 2 | Implement trend family | `edge_families/trend.py` | ~150 |
| 3 | Implement mean_reversion | `edge_families/mean_reversion.py` | ~120 |
| 4 | Implement carry | `edge_families/carry.py` | ~100 |
| 5 | Implement liquidation_squeeze | `edge_families/liquidation_squeeze.py` | ~100 |
| 6 | Implement event_breakout | `edge_families/event_breakout.py` | ~120 |
| 7 | Create ScoreComposer | `score_composer.py` | ~100 |
| 8 | Create SizingPipeline | `sizing_pipeline.py` | ~200 |
| 9 | Refactor DecisionEngine | `decision_engine.py` | ~400 (down from 1447) |
| 10 | Extract background loops | `loops/*.py` | ~300 |
| 11 | Refactor live_loop.py | `live_loop.py` | ~300 (down from 1895) |
| 12 | Write tests | `tests/` | ~500 |

**Total new code:** ~2,500 lines
**Net reduction in existing files:** ~1,500 lines

---

## Success Metrics

1. **Code quality:** DecisionEngine < 500 lines (down from 1447)
2. **Test coverage:** >90% for new modules
3. **Performance:** No increase in evaluate() latency
4. **Attribution:** Each signal tagged with winning family
5. **Maintainability:** New families can be added without touching existing code
