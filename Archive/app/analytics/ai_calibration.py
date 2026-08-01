"""AI Calibration Tracker — analyzes AI decision outcomes for reliability.

Reads from AIOoutcomeLogger's Redis data and produces calibration reports:
  - Reliability chart: AI said X% → actually won Y%
  - Precision/Recall by confidence bucket
  - Quant-only vs AI-blended performance comparison

Usage:
    python -m app.analytics.ai_calibration
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from loguru import logger as log_logger

from app.alpha.ai_outcome_logger import AIOoutcomeLogger


class AICalibrationTracker:
    """Analyze AI decision outcomes for calibration and reliability."""

    # Confidence buckets for calibration analysis
    BUCKETS = [
        (0, 30, "STRONG_REJECT"),
        (30, 50, "WEAK_REJECT"),
        (50, 65, "AMBIGUOUS_LOW"),
        (65, 80, "AMBIGUOUS_HIGH"),
        (80, 100, "STRONG_ACCEPT"),
    ]

    def __init__(self, outcome_logger: AIOoutcomeLogger) -> None:
        self.outcome_logger = outcome_logger

    async def analyze(self, symbol: str | None = None, limit: int = 1000) -> dict[str, Any]:
        """Full calibration analysis. Returns structured report."""
        data = await self.outcome_logger.get_calibration_data(symbol=symbol, limit=limit)

        if not data:
            return {
                "status": "no_data",
                "message": "No AI decisions with outcomes found. Run the bot in shadow/live mode to collect data.",
                "total_decisions": 0,
            }

        total = len(data)
        wins = sum(1 for d in data if self._is_win(d))
        losses = sum(1 for d in data if self._is_loss(d))
        flat = total - wins - losses

        # Per-bucket analysis
        bucket_stats = {}
        for lo, hi, label in self.BUCKETS:
            bucket_data = [d for d in data if lo <= d.get("ai_confidence", 0) < hi]
            if not bucket_data:
                bucket_stats[label] = {"count": 0, "win_rate": 0.0, "avg_pnl_pct": 0.0}
                continue
            bucket_wins = sum(1 for d in bucket_data if self._is_win(d))
            bucket_pnls = [d.get("outcome", {}).get("pnl_pct", 0) for d in bucket_data]
            bucket_stats[label] = {
                "count": len(bucket_data),
                "win_rate": round(bucket_wins / len(bucket_data), 4),
                "avg_pnl_pct": round(sum(bucket_pnls) / len(bucket_pnls), 4),
            }

        # Calibration error (ECE — Expected Calibration Error)
        ece = 0.0
        for lo, hi, label in self.BUCKETS:
            bucket_data = [d for d in data if lo <= d.get("ai_confidence", 0) < hi]
            if not bucket_data:
                continue
            avg_conf = sum(d.get("ai_confidence", 0) for d in bucket_data) / len(bucket_data)
            actual_wr = sum(1 for d in bucket_data if self._is_win(d)) / len(bucket_data)
            ece += len(bucket_data) / total * abs(avg_conf / 100.0 - actual_wr)

        # AI vs no-AI comparison
        ai_taken = [d for d in data if d.get("trade_taken")]
        ai_rejected = [d for d in data if not d.get("trade_taken")]

        return {
            "status": "ok",
            "total_decisions": total,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "overall_win_rate": round(wins / total, 4) if total > 0 else 0.0,
            "ece": round(ece, 4),  # lower = better calibrated
            "bucket_stats": bucket_stats,
            "ai_taken_count": len(ai_taken),
            "ai_rejected_count": len(ai_rejected),
            "recommendation": self._recommendation(ece, bucket_stats),
        }

    def _is_win(self, record: dict) -> bool:
        outcome = record.get("outcome")
        if not outcome:
            return False
        pnl = float(outcome.get("pnl_pct", 0))
        return pnl > 0

    def _is_loss(self, record: dict) -> bool:
        outcome = record.get("outcome")
        if not outcome:
            return False
        pnl = float(outcome.get("pnl_pct", 0))
        return pnl < 0

    def _recommendation(self, ece: float, bucket_stats: dict) -> str:
        if ece < 0.05:
            return "WELL_CALIBRATED — AI confidence scores are reliable predictors of actual outcomes."
        elif ece < 0.15:
            return "MODERATELY_CALIBRATED — AI is somewhat predictive but confidence scores need recalibration."
        else:
            # Check if AI is consistently wrong
            high_conf = bucket_stats.get("STRONG_ACCEPT", {})
            if high_conf.get("count", 0) > 10 and high_conf.get("win_rate", 0) < 0.4:
                return "ANTI_CALIBRATED — High-confidence AI predictions are WRONG more often than right. Consider inverting or removing the AI gate."
            return "POORLY_CALIBRATED — AI confidence scores do not predict outcomes. Consider removing the mandatory AI gate."


async def main() -> None:
    """CLI entry point for AI calibration analysis."""
    import argparse
    from app.core.dependencies import startup, get_redis

    parser = argparse.ArgumentParser(description="AI Calibration Tracker")
    parser.add_argument("--symbol", help="Filter by symbol")
    parser.add_argument("--limit", type=int, default=1000, help="Max records to analyze")
    args = parser.parse_args()

    await startup()
    redis = get_redis()
    outcome_log = AIOoutcomeLogger(redis)
    tracker = AICalibrationTracker(outcome_log)

    report = await tracker.analyze(symbol=args.symbol, limit=args.limit)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
