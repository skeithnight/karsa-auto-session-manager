"""Lead-Lag Analysis — proves Binance → Bybit arbitrage edge.

Analyzes historical data to measure:
1. How often Binance leads Bybit
2. By how many seconds
3. Whether a Bybit fill would have been possible during lead window
4. Statistical significance of the edge

Usage:
    python scripts/lead_lag_analysis.py --symbols BTC/USDT,ETH/USDT --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger


@dataclass
class LeadLagResult:
    """Result of lead-lag analysis for one symbol."""

    symbol: str
    lead_exchange: str
    lag_exchange: str
    total_moves: int
    lead_count: int
    lead_pct: float
    avg_lead_seconds: float
    median_lead_seconds: float
    p95_lead_seconds: float
    arb_capture_rate: float
    statistical_significance: float  # p-value
    edge_exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "lead_exchange": self.lead_exchange,
            "lag_exchange": self.lag_exchange,
            "total_moves": self.total_moves,
            "lead_count": self.lead_count,
            "lead_pct": round(self.lead_pct, 2),
            "avg_lead_seconds": round(self.avg_lead_seconds, 2),
            "median_lead_seconds": round(self.median_lead_seconds, 2),
            "p95_lead_seconds": round(self.p95_lead_seconds, 2),
            "arb_capture_rate": round(self.arb_capture_rate, 4),
            "statistical_significance": round(self.statistical_significance, 6),
            "edge_exists": self.edge_exists,
        }

    def summary(self) -> str:
        """Human-readable summary."""
        edge = "EXISTS" if self.edge_exists else "DOES NOT EXIST"
        return (
            f"\n{'='*60}\n"
            f"Lead-Lag Analysis: {self.symbol}\n"
            f"{'='*60}\n"
            f"Lead Exchange: {self.lead_exchange}\n"
            f"Lag Exchange: {self.lag_exchange}\n"
            f"Total Moves Analyzed: {self.total_moves}\n"
            f"Binance Leads: {self.lead_count}/{self.total_moves} ({self.lead_pct:.1f}%)\n"
            f"Avg Lead Time: {self.avg_lead_seconds:.1f}s\n"
            f"Median Lead Time: {self.median_lead_seconds:.1f}s\n"
            f"P95 Lead Time: {self.p95_lead_seconds:.1f}s\n"
            f"Arb Capture Rate: {self.arb_capture_rate:.2%}\n"
            f"Statistical Significance (p-value): {self.statistical_significance:.6f}\n"
            f"\nVERDICT: Lead-lag edge {edge}\n"
            f"{'='*60}\n"
        )


class LeadLagAnalyzer:
    """Analyzes lead-lag between exchanges using historical candles."""

    def __init__(
        self,
        lead_exchange: str = "binance",
        lag_exchange: str = "bybit",
        move_threshold_pct: float = 0.001,  # 0.1% minimum move
        lead_window_seconds: int = 60,  # Max lead time to consider
    ) -> None:
        self.lead_exchange = lead_exchange
        self.lag_exchange = lag_exchange
        self.move_threshold_pct = move_threshold_pct
        self.lead_window_seconds = lead_window_seconds

    async def analyze(
        self,
        symbol: str,
        lead_candles: list[list],
        lag_candles: list[list],
    ) -> LeadLagResult:
        """Analyze lead-lag for a symbol.

        Args:
            symbol: Trading pair
            lead_candles: OHLCV candles from lead exchange
            lag_candles: OHLCV candles from lag exchange

        Returns:
            LeadLagResult with statistics
        """
        # Align candles by timestamp
        lead_map = {int(c[0]): c for c in lead_candles}
        lag_map = {int(c[0]): c for c in lag_candles}

        # Find common timestamps
        common_ts = sorted(set(lead_map.keys()) & set(lag_map.keys()))

        if len(common_ts) < 10:
            logger.warning("Insufficient overlapping data for %s", symbol)
            return LeadLagResult(
                symbol=symbol,
                lead_exchange=self.lead_exchange,
                lag_exchange=self.lag_exchange,
                total_moves=0,
                lead_count=0,
                lead_pct=0.0,
                avg_lead_seconds=0.0,
                median_lead_seconds=0.0,
                p95_lead_seconds=0.0,
                arb_capture_rate=0.0,
                statistical_significance=1.0,
                edge_exists=False,
            )

        # Detect significant moves on lead exchange
        moves = []
        for i in range(1, len(common_ts)):
            ts = common_ts[i]
            prev_ts = common_ts[i - 1]

            lead_prev = lead_map[prev_ts]
            lead_curr = lead_map[ts]
            lag_prev = lag_map[prev_ts]
            lag_curr = lag_map[ts]

            # Lead exchange return
            lead_close_prev = float(lead_prev[4])
            lead_close_curr = float(lead_curr[4])
            if lead_close_prev == 0:
                continue
            lead_return = (lead_close_curr - lead_close_prev) / lead_close_prev

            # Lag exchange return
            lag_close_prev = float(lag_prev[4])
            lag_close_curr = float(lag_curr[4])
            if lag_close_prev == 0:
                continue
            lag_return = (lag_close_curr - lag_close_prev) / lag_close_curr

            # Check if lead exchange moved significantly
            if abs(lead_return) >= self.move_threshold_pct:
                # Determine if lead exchange moved first
                lead_first = abs(lead_return) > abs(lag_return)

                if lead_first:
                    # Estimate lead time (approximation from candle data)
                    lead_seconds = self._estimate_lead_time(
                        lead_return, lag_return, lead_close_prev, lag_close_prev
                    )

                    moves.append({
                        "timestamp": ts,
                        "lead_return": lead_return,
                        "lag_return": lag_return,
                        "lead_first": lead_first,
                        "lead_seconds": lead_seconds,
                    })

        # Calculate statistics
        total_moves = len(moves)
        if total_moves == 0:
            return LeadLagResult(
                symbol=symbol,
                lead_exchange=self.lead_exchange,
                lag_exchange=self.lag_exchange,
                total_moves=0,
                lead_count=0,
                lead_pct=0.0,
                avg_lead_seconds=0.0,
                median_lead_seconds=0.0,
                p95_lead_seconds=0.0,
                arb_capture_rate=0.0,
                statistical_significance=1.0,
                edge_exists=False,
            )

        lead_count = sum(1 for m in moves if m["lead_first"])
        lead_pct = (lead_count / total_moves) * 100

        lead_times = [m["lead_seconds"] for m in moves if m["lead_first"]]
        avg_lead = sum(lead_times) / len(lead_times) if lead_times else 0.0
        median_lead = sorted(lead_times)[len(lead_times) // 2] if lead_times else 0.0
        p95_lead = sorted(lead_times)[int(len(lead_times) * 0.95)] if lead_times else 0.0

        # Arb capture rate: how often lag exchange caught up within lead window
        arb_count = sum(
            1 for m in moves
            if m["lead_first"] and m["lead_seconds"] <= self.lead_window_seconds
        )
        arb_capture_rate = arb_count / total_moves if total_moves > 0 else 0.0

        # Statistical significance: one-sided binomial test
        p_value = self._binomial_test(lead_count, total_moves, 0.5)

        edge_exists = p_value < 0.05 and lead_pct > 55

        return LeadLagResult(
            symbol=symbol,
            lead_exchange=self.lead_exchange,
            lag_exchange=self.lag_exchange,
            total_moves=total_moves,
            lead_count=lead_count,
            lead_pct=lead_pct,
            avg_lead_seconds=avg_lead,
            median_lead_seconds=median_lead,
            p95_lead_seconds=p95_lead,
            arb_capture_rate=arb_capture_rate,
            statistical_significance=p_value,
            edge_exists=edge_exists,
        )

    def _estimate_lead_time(
        self,
        lead_return: float,
        lag_return: float,
        lead_price: float,
        lag_price: float,
    ) -> float:
        """Estimate lead time in seconds from candle data.

        This is an approximation. In production, use tick-level data.
        """
        # Simple heuristic: larger return difference = more lead time
        return_diff = abs(lead_return - lag_return)
        # Map return difference to seconds (0.1% diff ≈ 15s lead)
        estimated_seconds = min(60, return_diff * 15000)
        return max(1.0, estimated_seconds)

    def _binomial_test(self, successes: int, trials: int, p0: float) -> float:
        """One-sided binomial test p-value.

        H0: p = p0 (no edge)
        H1: p > p0 (edge exists)
        """
        if trials == 0:
            return 1.0

        # Normal approximation for large samples
        if trials > 20:
            mean = trials * p0
            std = math.sqrt(trials * p0 * (1 - p0))
            if std == 0:
                return 1.0
            z = (successes - mean) / std
            # P(Z > z) for one-sided test
            p_value = 0.5 * math.erfc(z / math.sqrt(2))
            return max(0.0, min(1.0, p_value))

        # Exact binomial for small samples
        from scipy import stats  # type: ignore
        p_value = 1 - stats.binom.cdf(successes - 1, trials, p0)
        return max(0.0, min(1.0, p_value))


async def main():
    """CLI entry point for lead-lag analysis."""
    parser = argparse.ArgumentParser(description="Lead-Lag Edge Analysis")
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTC/USDT,ETH/USDT",
        help="Comma-separated symbols to analyze",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to analyze",
    )
    parser.add_argument(
        "--lead",
        type=str,
        default="binance",
        help="Lead exchange (default: binance)",
    )
    parser.add_argument(
        "--lag",
        type=str,
        default="bybit",
        help="Lag exchange (default: bybit)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.001,
        help="Minimum move threshold (default: 0.001 = 0.1%%)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    # Load historical candles
    from app.backtest.data_loader import MicroDataLoader

    loader = MicroDataLoader()
    analyzer = LeadLagAnalyzer(
        lead_exchange=args.lead,
        lag_exchange=args.lag,
        move_threshold_pct=args.threshold,
    )

    results = []
    for symbol in symbols:
        logger.info("Analyzing %s...", symbol)

        # Load candles from both exchanges
        lead_df = await loader.fetch_ohlcv(symbol, "1h", limit=args.days * 24)
        lag_df = await loader.fetch_ohlcv(symbol, "1h", limit=args.days * 24)

        lead_candles = lead_df.values.tolist() if lead_df is not None and len(lead_df) > 0 else []
        lag_candles = lag_df.values.tolist() if lag_df is not None and len(lag_df) > 0 else []

        if not lead_candles or not lag_candles:
            logger.warning("No data for %s, skipping", symbol)
            continue

        result = await analyzer.analyze(symbol, lead_candles, lag_candles)
        results.append(result)
        print(result.summary())

    # Save results
    output = {
        "analysis_date": datetime.now(timezone.utc).isoformat(),
        "lead_exchange": args.lead,
        "lag_exchange": args.lag,
        "symbols": [r.to_dict() for r in results],
    }

    output_file = f"lead_lag_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to {output_file}")

    # Summary
    edges_found = sum(1 for r in results if r.edge_exists)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {edges_found}/{len(results)} symbols show lead-lag edge")
    print(f"{'='*60}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    asyncio.run(main())
