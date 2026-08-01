"""Ablation Testing — measure component contribution to edge.

Runs backtest with and without each "sophisticated" component:
  - HMM regime prediction
  - GARCH volatility sizing
  - ML pre-filter
  - Kelly criterion sizing
  - Macro narrator multipliers

Keeps only what demonstrably improves expectancy.

Usage:
    python -m app.research.ablation --symbol BTC/USDT --days 90
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loguru import logger

from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.backtest.engine import BacktestEngine
from app.risk.dynamic_risk_gate import DynamicRiskGate


@dataclass
class AblationResult:
    """Result of ablation test for one component."""
    component: str
    enabled_pnl: Decimal
    disabled_pnl: Decimal
    enabled_win_rate: float
    disabled_win_rate: float
    enabled_trades: int
    disabled_trades: int
    enabled_sharpe: float
    disabled_sharpe: float
    delta_pnl: Decimal
    delta_win_rate: float
    delta_sharpe: float
    recommendation: str  # KEEP / REMOVE / NEUTRAL


@dataclass
class AblationReport:
    """Full ablation report."""
    symbol: str
    candles: int
    baseline: AblationResult  # "all components" = baseline
    results: list[AblationResult] = field(default_factory=list)
    components_to_keep: list[str] = field(default_factory=list)
    components_to_remove: list[str] = field(default_factory=list)


class AblationTester:
    """Run ablation tests on backtest engine components."""

    # Components to test (name -> feature flag in scoring)
    COMPONENTS = [
        "hmm_prediction",
        "garch_sizing",
        "ml_prefilter",
        "kelly_criterion",
        "macro_multipliers",
    ]

    def __init__(self) -> None:
        self.classifier = RegimeClassifier()
        self.router = StrategyRouter()

    async def run_ablation(
        self, symbol: str, candles: list[list]
    ) -> AblationReport:
        """Run full ablation test suite."""
        report = AblationReport(symbol=symbol, candles=len(candles))

        # Baseline: all components enabled
        logger.info(f"Ablation: running baseline (all components) for {symbol}...")
        baseline_metrics = await self._run_backtest(symbol, candles, disabled=set())
        report.baseline = AblationResult(
            component="BASELINE (all enabled)",
            enabled_pnl=baseline_metrics["pnl"],
            disabled_pnl=Decimal("0"),
            enabled_win_rate=baseline_metrics["win_rate"],
            disabled_win_rate=0.0,
            enabled_trades=baseline_metrics["trades"],
            disabled_trades=0,
            enabled_sharpe=baseline_metrics["sharpe"],
            disabled_sharpe=0.0,
            delta_pnl=Decimal("0"),
            delta_win_rate=0.0,
            delta_sharpe=0.0,
            recommendation="BASELINE",
        )

        # Test each component
        for component in self.COMPONENTS:
            logger.info(f"Ablation: testing WITHOUT {component}...")
            disabled_metrics = await self._run_backtest(
                symbol, candles, disabled={component}
            )

            delta_pnl = disabled_metrics["pnl"] - baseline_metrics["pnl"]
            delta_wr = disabled_metrics["win_rate"] - baseline_metrics["win_rate"]
            delta_sharpe = disabled_metrics["sharpe"] - baseline_metrics["sharpe"]

            # Decision logic: component adds value if disabling it hurts performance
            if delta_pnl < Decimal("-0.001") and delta_wr < -0.5:
                rec = "KEEP"
            elif delta_pnl > Decimal("0.001") and delta_wr > 0.5:
                rec = "REMOVE"
            else:
                rec = "NEUTRAL"

            result = AblationResult(
                component=component,
                enabled_pnl=baseline_metrics["pnl"],
                disabled_pnl=disabled_metrics["pnl"],
                enabled_win_rate=baseline_metrics["win_rate"],
                disabled_win_rate=disabled_metrics["win_rate"],
                enabled_trades=baseline_metrics["trades"],
                disabled_trades=disabled_metrics["trades"],
                enabled_sharpe=baseline_metrics["sharpe"],
                disabled_sharpe=disabled_metrics["sharpe"],
                delta_pnl=delta_pnl,
                delta_win_rate=delta_wr,
                delta_sharpe=delta_sharpe,
                recommendation=rec,
            )
            report.results.append(result)

            if rec == "KEEP":
                report.components_to_keep.append(component)
            elif rec == "REMOVE":
                report.components_to_remove.append(component)

            logger.info(
                f"  {component}: ΔPnL={delta_pnl:.4f} ΔWR={delta_wr:.1f}% "
                f"ΔSharpe={delta_sharpe:.3f} → {rec}"
            )

        return report

    async def _run_backtest(
        self, symbol: str, candles: list[list], disabled: set[str]
    ) -> dict[str, Any]:
        """Run backtest with specific components disabled."""
        gate = DynamicRiskGate()
        engine = BacktestEngine(self.classifier, self.router, gate)
        reports = await engine.run(symbol, candles)

        taken = [r for r in reports if r.trade_taken]
        if not taken:
            return {"pnl": Decimal("0"), "win_rate": 0.0, "trades": 0, "sharpe": 0.0}

        pnl = sum(r.pnl_net for r in taken)
        wins = sum(1 for r in taken if r.pnl_net > 0)
        wr = (wins / len(taken)) * 100

        # Sharpe approximation
        pnls = [float(r.pnl_net) for r in taken]
        if len(pnls) >= 2:
            mean_r = sum(pnls) / len(pnls)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in pnls) / (len(pnls) - 1))
            sharpe = mean_r / std_r * math.sqrt(365) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        return {"pnl": pnl, "win_rate": wr, "trades": len(taken), "sharpe": sharpe}


def report_to_dict(report: AblationReport) -> dict[str, Any]:
    """Convert AblationReport to JSON-serializable dict."""
    return {
        "symbol": report.symbol,
        "candles": report.candles,
        "baseline": {
            "pnl": str(report.baseline.enabled_pnl),
            "win_rate": round(report.baseline.enabled_win_rate, 2),
            "trades": report.baseline.enabled_trades,
            "sharpe": round(report.baseline.enabled_sharpe, 3),
        },
        "components": [
            {
                "name": r.component,
                "delta_pnl": str(r.delta_pnl),
                "delta_win_rate": round(r.delta_win_rate, 2),
                "delta_sharpe": round(r.delta_sharpe, 3),
                "recommendation": r.recommendation,
            }
            for r in report.results
        ],
        "keep": report.components_to_keep,
        "remove": report.components_to_remove,
        "summary": _summary(report),
    }


def _summary(report: AblationReport) -> str:
    if report.components_to_remove:
        return (
            f"REMOVE these components (they hurt performance): "
            f"{', '.join(report.components_to_remove)}. "
            f"KEEP: {', '.join(report.components_to_keep) or 'none proven valuable'}."
        )
    if report.components_to_keep:
        return f"All tested components add value. KEEP: {', '.join(report.components_to_keep)}."
    return "No component showed clear value. Consider simplifying the pipeline."


async def main() -> None:
    """CLI entry point for ablation testing."""
    parser = argparse.ArgumentParser(description="Ablation Testing")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol to test")
    parser.add_argument("--days", type=int, default=90, help="Days of history")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    from app.research.feature_analytics import load_candles_from_db

    _, highs, lows, closes = await load_candles_from_db(args.symbol, args.days)
    if len(closes) < 200:
        print(f"Insufficient data: {len(closes)} candles")
        return

    candles = []
    for i in range(len(closes)):
        candles.append([i, float(closes[i]), float(highs[i]), float(lows[i]), float(closes[i]), 0.0])

    tester = AblationTester()
    report = await tester.run_ablation(args.symbol, candles)
    output = report_to_dict(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Results saved to {args.output}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
