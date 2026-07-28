"""Demo: Karsa v3.5 Decision Evaluation Graph

Shows the full pipeline working together:
1. EvidenceTTL tracking freshness
2. Multiple evaluators (Trend, Volatility, Momentum) running
3. Fusion layer combining results
4. Decision attribution

Run with:
    uv run python -m app.alpha.evaluators.demo_v35
"""
from __future__ import annotations

import time
from decimal import Decimal

import numpy as np

from app.alpha.evaluators.fusion import EvaluatorFusion
from app.alpha.evaluators.momentum_evaluator import MomentumEvaluator
from app.alpha.evaluators.trend_evaluator import TrendEvaluator
from app.alpha.evaluators.volatility_evaluator import VolatilityEvaluator
from app.core.evidence_ttl import EvidenceTTLRegistry
from app.core.feature_extractor import FeatureVector
from app.core.market_snapshot import MarketSnapshot


def create_bullish_features() -> FeatureVector:
    """Create features representing a bullish market."""
    return FeatureVector(
        close=105000.0,
        ema_20=104500.0,
        ema_200=98000.0,
        sma_20=104200.0,
        atr=1800.0,
        atr_pct=1.71,
        rsi_14=62.0,
        adx_14=32.0,
        hurst=0.62,
        funding_rate=0.01,
        oi_change=0.02,
        orderbook_delta=0.15,
        cvd_slope=0.08,
        spread_pct=0.0005,
    )


def create_bearish_features() -> FeatureVector:
    """Create features representing a bearish market."""
    return FeatureVector(
        close=95000.0,
        ema_20=96000.0,
        ema_200=102000.0,
        sma_20=96500.0,
        atr=2200.0,
        atr_pct=2.32,
        rsi_14=38.0,
        adx_14=28.0,
        hurst=0.45,
        funding_rate=-0.02,
        oi_change=-0.01,
        orderbook_delta=-0.12,
        cvd_slope=-0.1,
        spread_pct=0.0008,
    )


def create_snapshot(closes: list[float]) -> MarketSnapshot:
    """Create a MarketSnapshot from close prices."""
    candles = np.array([
        [i * 3600000, c * 0.99, c * 1.01, c * 0.98, c, 1000000]
        for i, c in enumerate(closes)
    ])
    return MarketSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=int(time.time() * 1000),
        candles=candles,
    )


def demo_evidence_ttl():
    """Demo: Evidence TTL tracking freshness."""
    print("\n" + "=" * 60)
    print("DEMO 1: Evidence TTL Tracking")
    print("=" * 60)

    registry = EvidenceTTLRegistry()

    # Collect evidence with different TTLs
    registry.collect("orderbook", 1.0, 15.0, "Strong bid support", ttl_seconds=0.5)
    registry.collect("funding", -1.0, 10.0, "Negative funding", ttl_seconds=28800)
    registry.collect("cvd", 1.0, 12.0, "Buyers in control", ttl_seconds=1.0)
    registry.collect("regime", 1.0, 20.0, "TREND_BULL regime", ttl_seconds=300)

    print("\nEvidence collected:")
    for e in registry.get_all():
        print(f"  {e.source:12} | TTL={e.ttl_seconds:8.1f}s | "
              f"Freshness={e.freshness.value:10} | Penalty={e.freshness_penalty:.2f}")

    # Compute scores
    raw_score = registry.compute_raw_score()
    weighted_score = registry.compute_weighted_score()

    print(f"\nRaw score: {raw_score:.1f}")
    print(f"Weighted score (with freshness): {weighted_score:.1f}")
    print(f"Freshness adjustment: {(weighted_score/raw_score - 1) * 100:.1f}%")

    # Freshness summary
    summary = registry.get_freshness_summary()
    print("\nFreshness summary:")
    for source, stats in summary.items():
        print(f"  {source:12} | count={stats['count']} | "
              f"fresh={stats['fresh_count']} | expired={stats['expired_count']}")


def demo_evaluators():
    """Demo: Independent evaluators running on the same data."""
    print("\n" + "=" * 60)
    print("DEMO 2: Independent Evaluators")
    print("=" * 60)

    features = create_bullish_features()
    snapshot = create_snapshot([
        100000, 101000, 102000, 103000, 104000,
        104500, 104800, 105000, 105200, 105000,
    ])

    # Run each evaluator
    trend_eval = TrendEvaluator()
    vol_eval = VolatilityEvaluator()
    mom_eval = MomentumEvaluator()

    trend_result = trend_eval.evaluate(features, snapshot)
    vol_result = vol_eval.evaluate(features, snapshot)
    mom_result = mom_eval.evaluate(features, snapshot)

    print("\nTrend Evaluator:")
    print(f"  Direction: {'BULLISH' if trend_result.direction > 0 else 'BEARISH'}")
    print(f"  Weight: {trend_result.weight:.1f}")
    print(f"  Confidence: {trend_result.confidence:.2f}")
    print(f"  Reason: {trend_result.reason}")
    print(f"  Contributions: {trend_result.contributions}")

    print("\nVolatility Evaluator:")
    print(f"  Direction: {'FAVORABLE' if vol_result.direction > 0 else 'UNFAVORABLE'}")
    print(f"  Weight: {vol_result.weight:.1f}")
    print(f"  Confidence: {vol_result.confidence:.2f}")
    print(f"  Reason: {vol_result.reason}")

    print("\nMomentum Evaluator:")
    print(f"  Direction: {'BULLISH' if mom_result.direction > 0 else 'BEARISH'}")
    print(f"  Weight: {mom_result.weight:.1f}")
    print(f"  Confidence: {mom_result.confidence:.2f}")
    print(f"  Reason: {mom_result.reason}")

    return trend_result, vol_result, mom_result


def demo_fusion(trend_result, vol_result, mom_result):
    """Demo: Fusion layer combining evaluator results."""
    print("\n" + "=" * 60)
    print("DEMO 3: Fusion Layer")
    print("=" * 60)

    fusion = EvaluatorFusion()

    # Fuse all evaluators
    result = fusion.fuse(
        [trend_result, vol_result, mom_result],
        ["trend", "volatility", "momentum"],
    )

    print(f"\nFused Result:")
    print(f"  Direction: {'BULLISH' if result.direction > 0 else 'BEARISH'} ({result.direction:.3f})")
    print(f"  Weight: {result.weight:.1f}")
    print(f"  Confidence: {result.confidence:.2f}")
    print(f"  Signal Strength: {result.signal_strength:.1f}")
    print(f"  Recommendation: {result.recommendation}")
    print(f"  Is Actionable: {result.is_actionable}")
    print(f"  Dominant Evaluator: {result.dominant_evaluator}")

    print(f"\nDecision Attribution:")
    for name, pct in sorted(result.attribution.items(), key=lambda x: -x[1]):
        print(f"  {name:12} | {pct:5.1f}%")

    print(f"\nReasoning:")
    print(f"  {result.reasoning}")

    return result


def demo_execution_intent():
    """Demo: Immutable ExecutionIntent."""
    print("\n" + "=" * 60)
    print("DEMO 4: Immutable ExecutionIntent")
    print("=" * 60)

    from app.core.execution_intent import (
        ExecutionIntent,
        IntentConstraints,
        IntentGoal,
        IntentHistory,
        IntentResult,
        IntentUrgency,
    )

    # Create initial intent
    intent_v1 = ExecutionIntent(
        symbol="BTCUSDT",
        side="LONG",
        quantity=Decimal("0.001"),
        goal=IntentGoal.OPEN_POSITION,
        constraints=IntentConstraints(
            max_slippage_bps=25,
            time_budget_seconds=12,
            maker_preferred=True,
        ),
        reason="Strong bullish signal from evaluator fusion",
    )

    print(f"\nIntent v1:")
    print(f"  ID: {intent_v1.intent_id}")
    print(f"  Version: {intent_v1.version}")
    print(f"  Goal: {intent_v1.goal.value}")
    print(f"  Symbol: {intent_v1.symbol}")
    print(f"  Side: {intent_v1.side}")

    # Create updated version (reduce exposure)
    intent_v2 = intent_v1.with_updates(
        goal=IntentGoal.REDUCE_EXPOSURE,
        target_exposure=Decimal("0.5"),
        reason="Taking partial profit at +2R",
    )

    print(f"\nIntent v2 (after update):")
    print(f"  ID: {intent_v2.intent_id}")
    print(f"  Version: {intent_v2.version}")
    print(f"  Parent ID: {intent_v2.parent_id}")
    print(f"  Goal: {intent_v2.goal.value}")

    # Mark executed
    result = IntentResult(
        filled_quantity=Decimal("0.0005"),
        average_price=Decimal("105000"),
        total_fees=Decimal("0.525"),
        slippage_bps=2.5,
        maker_fill=True,
        fill_time_ms=150.0,
        order_ids=["ord_123", "ord_456"],
    )
    intent_v3 = intent_v2.mark_executed(result)

    print(f"\nIntent v3 (executed):")
    print(f"  ID: {intent_v3.intent_id}")
    print(f"  Version: {intent_v3.version}")
    print(f"  Executed at: {intent_v3.executed_at}")
    print(f"  Result:")
    print(f"    Filled: {intent_v3.result.filled_quantity}")
    print(f"    Avg Price: {intent_v3.result.average_price}")
    print(f"    Fees: {intent_v3.result.total_fees}")
    print(f"    Slippage: {intent_v3.result.slippage_bps} bps")
    print(f"    Maker Fill: {intent_v3.result.maker_fill}")

    # Track in history
    history = IntentHistory()
    history.add(intent_v1)
    history.add(intent_v2)
    history.add(intent_v3)

    chain = history.get_chain(intent_v3.intent_id)
    print(f"\nIntent History ({len(chain)} versions):")
    for intent in chain:
        print(f"  v{intent.version}: {intent.intent_id} -> {intent.goal.value}")


def main():
    """Run all demos."""
    print("\n" + "#" * 60)
    print("# Karsa v3.5 Decision Evaluation Graph - Demo")
    print("#" * 60)

    # Demo 1: Evidence TTL
    demo_evidence_ttl()

    # Demo 2: Independent Evaluators
    trend_result, vol_result, mom_result = demo_evaluators()

    # Demo 3: Fusion Layer
    fusion_result = demo_fusion(trend_result, vol_result, mom_result)

    # Demo 4: Execution Intent
    demo_execution_intent()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("\nAll v3.5 prototypes working together:")
    print("  ✅ EvidenceTTL - Freshness tracking for evidence sources")
    print("  ✅ TrendEvaluator - Independent trend evaluation")
    print("  ✅ VolatilityEvaluator - Volatility regime assessment")
    print("  ✅ MomentumEvaluator - Momentum analysis")
    print("  ✅ EvaluatorFusion - Combines evaluator results")
    print("  ✅ ExecutionIntent - Immutable execution contracts")
    print("  ✅ IntentHistory - Audit trail for version chains")
    print("\nArchitecture validated: Evidence → Evaluators → Fusion → Intent")


if __name__ == "__main__":
    main()
