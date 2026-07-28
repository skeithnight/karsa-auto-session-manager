"""Monte Carlo Resampling — statistical stability check on backtest results.

Resamples trade sequence N times to estimate:
  - Confidence intervals on PnL, Sharpe, max drawdown
  - Probability of ruin (PnL < 0 after resampling)
  - Whether measured edge is stable or artifact of trade ordering

Usage:
    python -m app.backtest.monte_carlo --trades-file results.json --iterations 10000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loguru import logger


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo resampling."""
    iterations: int
    trade_count: int
    # PnL distribution
    mean_pnl: float
    median_pnl: float
    pnl_5th: float  # 5th percentile (worst case)
    pnl_95th: float  # 95th percentile (best case)
    pnl_std: float
    # Sharpe distribution
    mean_sharpe: float
    sharpe_5th: float
    sharpe_95th: float
    # Drawdown distribution
    mean_max_dd: float
    max_dd_95th: float  # 95th percentile worst drawdown
    # Risk metrics
    probability_of_ruin: float  # P(final PnL < 0)
    # Stability check
    edge_is_stable: bool  # True if >90% of resamples have positive PnL
    profit_factor: float
    expectancy_per_trade: float


def monte_carlo_resample(
    trade_pnls: list[float],
    n_iterations: int = 10000,
    seed: int | None = None,
) -> MonteCarloResult:
    """Resample trade sequence N times with replacement.

    Each iteration randomly reorders the trades and computes cumulative PnL,
    Sharpe, and max drawdown. This tells us whether the measured edge is
    robust to trade ordering (stable) or dependent on lucky sequencing.
    """
    if not trade_pnls:
        return MonteCarloResult(
            iterations=0, trade_count=0, mean_pnl=0, median_pnl=0,
            pnl_5th=0, pnl_95th=0, pnl_std=0, mean_sharpe=0,
            sharpe_5th=0, sharpe_95th=0, mean_max_dd=0, max_dd_95th=0,
            probability_of_ruin=0, edge_is_stable=False,
            profit_factor=0, expectancy_per_trade=0,
        )

    rng = random.Random(seed)
    n_trades = len(trade_pnls)

    # Compute per-trade stats
    wins = [p for p in trade_pnls if p > 0]
    losses = [abs(p) for p in trade_pnls if p < 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = sum(losses) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = sum(trade_pnls) / n_trades

    # Resample
    final_pnls = []
    sharpes = []
    max_dds = []
    ruin_count = 0

    for _ in range(n_iterations):
        # Random resample with replacement
        sample = [rng.choice(trade_pnls) for _ in range(n_trades)]

        # Cumulative PnL
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in sample:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        final_pnls.append(cumulative)
        max_dds.append(max_dd)

        # Sharpe (annualized approximation — assume 1 trade/day)
        mean_r = sum(sample) / n_trades
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in sample) / n_trades) if n_trades > 1 else 1.0
        sharpe = mean_r / std_r * math.sqrt(365) if std_r > 0 else 0.0
        sharpes.append(sharpe)

        if cumulative < 0:
            ruin_count += 1

    # Compute percentiles
    final_pnls.sort()
    sharpes.sort()
    max_dds.sort()

    def percentile(data: list[float], p: float) -> float:
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    mean_pnl = sum(final_pnls) / n_iterations
    std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in final_pnls) / n_iterations)

    edge_stable = (ruin_count / n_iterations) < 0.10  # <10% chance of ruin = stable

    return MonteCarloResult(
        iterations=n_iterations,
        trade_count=n_trades,
        mean_pnl=round(mean_pnl, 4),
        median_pnl=round(percentile(final_pnls, 0.5), 4),
        pnl_5th=round(percentile(final_pnls, 0.05), 4),
        pnl_95th=round(percentile(final_pnls, 0.95), 4),
        pnl_std=round(std_pnl, 4),
        mean_sharpe=round(sum(sharpes) / n_iterations, 3),
        sharpe_5th=round(percentile(sharpes, 0.05), 3),
        sharpe_95th=round(percentile(sharpes, 0.95), 3),
        mean_max_dd=round(sum(max_dds) / n_iterations, 4),
        max_dd_95th=round(percentile(max_dds, 0.95), 4),
        probability_of_ruin=round(ruin_count / n_iterations, 4),
        edge_is_stable=edge_stable,
        profit_factor=round(profit_factor, 3),
        expectancy_per_trade=round(expectancy, 6),
    )


def result_to_dict(result: MonteCarloResult) -> dict[str, Any]:
    """Convert MonteCarloResult to JSON-serializable dict."""
    return {
        "iterations": result.iterations,
        "trade_count": result.trade_count,
        "pnl": {
            "mean": result.mean_pnl,
            "median": result.median_pnl,
            "std": result.pnl_std,
            "p5": result.pnl_5th,
            "p95": result.pnl_95th,
        },
        "sharpe": {
            "mean": result.mean_sharpe,
            "p5": result.sharpe_5th,
            "p95": result.sharpe_95th,
        },
        "max_drawdown": {
            "mean": result.mean_max_dd,
            "p95": result.max_dd_95th,
        },
        "risk": {
            "probability_of_ruin": result.probability_of_ruin,
            "edge_is_stable": result.edge_is_stable,
        },
        "efficiency": {
            "profit_factor": result.profit_factor,
            "expectancy_per_trade": result.expectancy_per_trade,
        },
        "recommendation": _recommendation(result),
    }


def _recommendation(result: MonteCarloResult) -> str:
    if result.trade_count < 30:
        return "INSUFFICIENT_DATA — Need 30+ trades for reliable Monte Carlo analysis."
    if result.edge_is_stable and result.mean_sharpe > 1.0:
        return "STRONG_EDGE — Stable positive PnL across resamples with good Sharpe."
    if result.edge_is_stable and result.mean_sharpe > 0:
        return "WEAK_EDGE — Positive but low Sharpe. Edge exists but may not justify risk."
    if result.probability_of_ruin > 0.3:
        return "NO_EDGE — High probability of ruin. Strategy is not profitable over this sample."
    return "MARGINAL_EDGE — Some positive resamples but unstable. More data needed."


async def main() -> None:
    """CLI entry point for Monte Carlo analysis."""
    parser = argparse.ArgumentParser(description="Monte Carlo Resampling")
    parser.add_argument("--trades-file", required=True, help="JSON file with trade PnLs")
    parser.add_argument("--iterations", type=int, default=10000, help="Number of resamples")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    with open(args.trades_file) as f:
        data = json.load(f)

    # Accept list of floats or list of dicts with "pnl_net" key
    if isinstance(data, list) and data:
        if isinstance(data[0], dict):
            pnls = [float(t.get("pnl_net", 0)) for t in data]
        else:
            pnls = [float(t) for t in data]
    else:
        print("Invalid trades file format")
        return

    result = monte_carlo_resample(pnls, n_iterations=args.iterations)
    output = result_to_dict(result)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Results saved to {args.output}")
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
