# Karsa v3.5 Integration Guide

## Overview

This guide shows how to integrate the v3.5 Decision Evaluation Graph
into the existing DecisionEngine without requiring a full rewrite.

## Architecture

```
Existing Pipeline:
  MarketSnapshot → FeatureExtractor → StrategyRouter → Score → Gate

v3.5 Pipeline:
  MarketSnapshot → FeatureExtractor → Evaluators → Fusion → Score → Gate
```

## Integration Steps

### Step 1: Import ScoringBridge

In `app/consumer/decision_engine.py`, add the import:

```python
from app.alpha.evaluators.scoring_bridge import ScoringBridge
```

### Step 2: Initialize Bridge

In `DecisionEngine.__init__()`, add:

```python
# v3.5 evaluator bridge (disabled by default for safety)
self._scoring_bridge = ScoringBridge(enabled=False)
```

### Step 3: Replace Scoring Logic

In `DecisionEngine.evaluate()`, find the scoring section and replace:

```python
# OLD:
score = await self._router.evaluate_signal(...)

# NEW:
# Use v3.5 evaluators if enabled, otherwise fallback to existing scoring
score, fusion_result = await self._scoring_bridge.score(
    features,
    snapshot,
    regime,
    fallback_score=score,  # Existing score as fallback
)
```

### Step 4: Enable v3.5 (Optional)

To enable v3.5 scoring, set `enabled=True`:

```python
self._scoring_bridge = ScoringBridge(enabled=True)
```

Or enable at runtime:

```python
self._scoring_bridge.enable()
```

## Usage Example

```python
from app.alpha.evaluators.scoring_bridge import ScoringBridge
from app.alpha.regime_classifier import MarketRegime

# Initialize bridge
bridge = ScoringBridge(enabled=True)

# In your evaluation loop:
async def evaluate(symbol: str, arr: np.ndarray, regime: MarketRegime):
    # Extract features
    snapshot = MarketSnapshot(symbol=symbol, ...)
    store = FeatureStore(snapshot)
    features = FeatureExtractor.extract(store)

    # Get score from v3.5 evaluators
    score, fusion_result = await bridge.score(
        features,
        snapshot,
        regime,
        fallback_score=50.0,  # Default score if evaluators fail
    )

    # Check if we should trade
    if fusion_result and bridge.should_trade(fusion_result, min_score=75.0):
        print(f"Trading signal: {fusion_result.recommendation}")
        print(f"Confidence: {fusion_result.confidence:.2f}")
        print(f"Attribution: {fusion_result.attribution}")

    return score
```

## Fallback Behavior

The ScoringBridge is designed to be safe:

1. **Disabled by default** — Uses existing scoring unless explicitly enabled
2. **Graceful degradation** — Falls back to existing scoring if evaluators fail
3. **Optional integration** — Can be enabled/disabled at runtime

## Metrics

The bridge collects metrics for monitoring:

```python
metrics = bridge.get_metrics()
print(f"Evaluation time: {metrics.evaluation_time_ms:.1f}ms")
print(f"Evaluators used: {metrics.evaluator_count}")
print(f"Confidence: {metrics.confidence:.2f}")
print(f"Recommendation: {metrics.recommendation}")
```

## Regime-Specific Weights

The registry supports regime-specific evaluator weights:

```python
# In EvaluatorRegistry:
self._regime_weights = {
    MarketRegime.TREND_BULL: {"trend": 1.2, "momentum": 1.0, "volatility": 0.8},
    MarketRegime.RANGE: {"trend": 0.6, "momentum": 0.8, "volatility": 1.2},
    ...
}
```

## Custom Evaluators

You can register custom evaluators:

```python
from app.alpha.evaluators.registry import EvaluatorRegistry

class SentimentEvaluator:
    def evaluate(self, features, snapshot=None):
        # Your sentiment logic here
        return EvaluatorResult(direction=0.5, weight=50.0, confidence=0.7, reason="...")

registry = EvaluatorRegistry()
registry.register_evaluator("sentiment", SentimentEvaluator())
```

## Testing

Run the v3.5 test suite:

```bash
uv run pytest tests/test_v3_prototypes/ -v
```

## Rollback

If v3.5 causes issues, simply disable it:

```python
self._scoring_bridge.disable()
```

The system will automatically fall back to the existing scoring logic.
