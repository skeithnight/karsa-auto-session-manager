"""Walk-Forward Optimizer — train on window, test on held-out period.

Prevents overfitting by ensuring parameters that work in-sample
also work out-of-sample. Walks forward through time:
  [===========TRAIN============][===TEST===]
            [===========TRAIN============][===TEST===]
                      [===========TRAIN============][===TEST===]

Usage:
    python -m app.backtest.walk_forward --symbol BTC/USDT --days 180
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from loguru import logger

from app.alpha.regime_classifier import RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.backtest.engine import BacktestEngine
from app.risk.dynamic_risk_gate import DynamicRiskGate


@dataclass
class WindowResult:
    """Results for one walk-forward window."""
    window_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    best_sl_buffer: Decimal
    best_trail_mult: Decimal
    train_pnl: Decimal
    test_pnl: Decimal
    train_win_rate: float
    test_win_rate: float
    train_trades: int
    test_trades: int


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward optimization results."""
    symbol: str
    windows: list[WindowResult] = field(default_factory=list)
    oos_pnl: Decimal = Decimal("0")  # total out-of-sample PnL
    oos_trades: int = 0
    oos_wins: int = 0
    oos_win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_train_pnl: Decimal = Decimal("0")
    avg_test_pnl: Decimal = Decimal("0")
    overfit_score: float = 0.0  # ratio of train/test PnL — high = overfit
    robustness_score: float = 0.0  # 0-100, higher = more robust
    recommendation: str = ""  # human-readable assessment


class WalkForwardOptimizer:
    """Walk-forward parameter optimization with train/test splitting.

    For each window:
    1. Grid search on TRAIN period to find best parameters
    2. Run those parameters on TEST period (out-of-sample)
    3. Move window forward
    """

    def __init__(
        self,
        sl_buffers: list[Decimal] | None = None,
        trail_mults: list[Decimal] | None = None,
        train_ratio: float = 0.7,
        n_windows: int = 5,
    ) -> None:
        self.sl_buffers = sl_buffers or [
            Decimal("1.0"), Decimal("1.5"), Decimal("2.0"),
            Decimal("2.5"), Decimal("3.0"),
        ]
        self.trail_mults = trail_mults or [
            Decimal("1.5"), Decimal("2.0"), Decimal("2.5"),
            Decimal("3.0"), Decimal("4.0"),
        ]
        self.train_ratio = train_ratio
        self.n_windows = n_windows
        self.classifier = RegimeClassifier()
        self.router = StrategyRouter()

    async def optimize(self, symbol: str, candles: list[list]) -> WalkForwardResult:
        """Run walk-forward optimization on candle data."""
        result = WalkForwardResult(symbol=symbol)
        n = len(candles)

        if n < 200:
            logger.warning(f"WalkForward: insufficient candles for {symbol}: {n}")
            return result

        # Calculate window sizes
        window_size = n // self.n_windows
        train_size = int(window_size * self.train_ratio)
        test_size = window_size - train_size

        logger.info(
            f"WalkForward: {symbol} — {n} candles, {self.n_windows} windows, "
            f"train={train_size}, test={test_size}"
        )

        for w in range(self.n_windows):
            start = w * window_size
            train_end = start + train_size
            test_end = min(start + window_size, n)

            if test_end > n or train_end >= n:
                break

            train_candles = candles[start:train_end]
            test_candles = candles[train_end:test_end]

            # Grid search on train period
            best_params, train_pnl, train_wr, train_trades = await self._grid_search(
                symbol, train_candles
            )

            # Test on out-of-sample period
            test_pnl, test_wr, test_trades = await self._run_backtest(
                symbol, test_candles, best_params[0], best_params[1]
            )

            window = WindowResult(
                window_id=w,
                train_start=start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
                best_sl_buffer=best_params[0],
                best_trail_mult=best_params[1],
                train_pnl=train_pnl,
                test_pnl=test_pnl,
                train_win_rate=train_wr,
                test_win_rate=test_wr,
                train_trades=train_trades,
                test_trades=test_trades,
            )
            result.windows.append(window)

            logger.info(
                f"Window {w}: train PnL={train_pnl:.2f} ({train_wr:.0f}%) "
                f"→ test PnL={test_pnl:.2f} ({test_wr:.0f}%) "
                f"[sl={best_params[0]}, trail={best_params[1]}]"
            )

        # Aggregate OOS results
        if result.windows:
            result.oos_pnl = sum(w.test_pnl for w in result.windows)
            result.oos_trades = sum(w.test_trades for w in result.windows)
            avg_train = sum(w.train_pnl for w in result.windows) / len(result.windows)
            avg_test = sum(w.test_pnl for w in result.windows) / len(result.windows)
            result.avg_train_pnl = avg_train
            result.avg_test_pnl = avg_test

            # Overfit score: ratio of test/train PnL (1.0 = perfect generalization)
            if avg_train != 0:
                result.overfit_score = float(avg_test / avg_train)
            else:
                result.overfit_score = 0.0

            # OOS win rate (approximate from windows)
            total_wins = sum(
                int(w.test_win_rate / 100 * w.test_trades) for w in result.windows
            )
            result.oos_win_rate = (total_wins / result.oos_trades * 100) if result.oos_trades > 0 else 0.0

            # Sharpe approximation from window PnLs
            pnls = [float(w.test_pnl) for w in result.windows]
            if len(pnls) > 1:
                mean_pnl = sum(pnls) / len(pnls)
                std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls))
                result.sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0

            # Max drawdown from cumulative OOS PnL
            cumulative = Decimal("0")
            peak = Decimal("0")
            max_dd = Decimal("0")
            for w in result.windows:
                cumulative += w.test_pnl
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd
            result.max_drawdown = float(max_dd)

            # Robustness score: 100 = perfect generalization, 0 = terrible
            # Factors: overfit_score (70% weight), win rate consistency (20%), Sharpe (10%)
            of_score = max(0, min(100, result.overfit_score * 100))
            wr_score = max(0, min(100, result.oos_win_rate))
            sharpe_score = max(0, min(100, (result.sharpe + 1) * 50))  # normalize Sharpe to 0-100
            result.robustness_score = round(of_score * 0.7 + wr_score * 0.2 + sharpe_score * 0.1, 1)

            # Recommendation
            if result.robustness_score >= 70:
                result.recommendation = "ROBUST — strategy generalizes well to unseen data"
            elif result.robustness_score >= 40:
                result.recommendation = "MODERATE — some overfitting detected, use with caution"
            else:
                result.recommendation = "FRAGILE — significant overfitting, do not deploy"

        return result

    async def _grid_search(
        self, symbol: str, candles: list[list]
    ) -> tuple[tuple[Decimal, Decimal], Decimal, float, int]:
        """Grid search over parameter space. Returns (best_params, pnl, win_rate, trades)."""
        best_pnl = Decimal("-999999")
        best_params = (Decimal("2.0"), Decimal("2.5"))  # default
        best_wr = 0.0
        best_trades = 0

        for sl in self.sl_buffers:
            for trail in self.trail_mults:
                pnl, wr, trades = await self._run_backtest(symbol, candles, sl, trail)
                if pnl > best_pnl:
                    best_pnl = pnl
                    best_params = (sl, trail)
                    best_wr = wr
                    best_trades = trades

        return best_params, best_pnl, best_wr, best_trades

    async def _run_backtest(
        self, symbol: str, candles: list[list],
        sl_buffer: Decimal, trail_mult: Decimal,
    ) -> tuple[Decimal, float, int]:
        """Run backtest with specific parameters. Returns (pnl, win_rate, trades)."""
        gate = DynamicRiskGate(override_sl_buffer=sl_buffer, override_trail_mult=trail_mult)
        engine = BacktestEngine(self.classifier, self.router, gate)
        reports = await engine.run(symbol, candles)

        taken = [r for r in reports if r.trade_taken]
        if not taken:
            return Decimal("0"), 0.0, 0

        pnl = sum(r.pnl_net for r in taken)
        wins = sum(1 for r in taken if r.pnl_net > 0)
        wr = (wins / len(taken)) * 100

        return pnl, wr, len(taken)


async def main() -> None:
    """CLI entry point for walk-forward optimization."""
    parser = argparse.ArgumentParser(description="Walk-Forward Optimizer")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol to optimize")
    parser.add_argument("--days", type=int, default=180, help="Days of history")
    parser.add_argument("--windows", type=int, default=5, help="Number of walk-forward windows")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    # Load candles from DB
    from app.research.feature_analytics import load_candles_from_db
    _, highs, lows, closes = await load_candles_from_db(args.symbol, args.days)

    if len(closes) < 200:
        print(f"Insufficient data: {len(closes)} candles (need 200+)")
        return

    # Convert to candle format for backtest engine
    candles = []
    for i in range(len(closes)):
        candles.append([i, float(closes[i]), float(highs[i]), float(lows[i]), float(closes[i]), 0.0])

    optimizer = WalkForwardOptimizer(n_windows=args.windows)
    result = await optimizer.optimize(args.symbol, candles)

    report = {
        "symbol": result.symbol,
        "oos_pnl": str(result.oos_pnl),
        "oos_trades": result.oos_trades,
        "oos_win_rate": round(result.oos_win_rate, 2),
        "sharpe": round(result.sharpe, 3),
        "max_drawdown": round(result.max_drawdown, 2),
        "overfit_score": round(result.overfit_score, 3),
        "avg_train_pnl": str(result.avg_train_pnl),
        "avg_test_pnl": str(result.avg_test_pnl),
        "windows": [
            {
                "id": w.window_id,
                "best_sl": str(w.best_sl_buffer),
                "best_trail": str(w.best_trail_mult),
                "train_pnl": str(w.train_pnl),
                "test_pnl": str(w.test_pnl),
                "train_wr": round(w.train_win_rate, 1),
                "test_wr": round(w.test_win_rate, 1),
            }
            for w in result.windows
        ],
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Results saved to {args.output}")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
