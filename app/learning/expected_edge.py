"""Expected Edge Calculator (Sprint 3).

Calculates the statistical expectancy of a decision based on historical similarity.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("karsa.expected_edge")  # type: ignore[assignment]

from app.core.decision_context import DecisionContext
from app.learning.similarity_engine import SimilarityEngine


@dataclass
class EdgeProfile:
    expectancy: float
    win_rate: float
    sample_size: int
    similarity_score: float


class ExpectedEdgeCalculator:
    """Calculates statistical expectancy using SimilarityEngine and TradeMemory."""

    def __init__(self, similarity: SimilarityEngine, trade_memory: object) -> None:
        self.similarity = similarity
        self.memory = trade_memory

    async def calculate(self, context: DecisionContext) -> EdgeProfile:
        """Find similar past trades and calculate their average PnL (expectancy)."""
        # Fetch historical trades for this symbol
        # Real system: Query Postgres via DecisionRegistry.
        # Here we use TradeMemory (Redis) for real-time fast-path.
        # In a real system trade_memory interface might lack async get_recent type hinting.
        # Assuming self.memory has async get_recent(symbol, count).
        if not hasattr(self.memory, 'get_recent'):
            return EdgeProfile(expectancy=0.0, win_rate=0.0, sample_size=0, similarity_score=0.0)

        recent_trades = await self.memory.get_recent(context.symbol, count=50) # type: ignore

        if not recent_trades:
            return EdgeProfile(expectancy=0.0, win_rate=0.0, sample_size=0, similarity_score=0.0)

        similar_trades = []
        for t in recent_trades:
            hist_features = t.get("features", {})
            if hist_features:
                sim = self.similarity.compute_similarity(context.features.__dict__, hist_features)
                if sim > 0.8: # Threshold for "similar"
                    similar_trades.append((t, sim))

        if not similar_trades:
            return EdgeProfile(expectancy=0.0, win_rate=0.0, sample_size=0, similarity_score=0.0)

        total_pnl = sum(t[0].get("pnl_pct", 0.0) for t in similar_trades)
        wins = sum(1 for t in similar_trades if t[0].get("pnl_pct", 0.0) > 0)

        expectancy = total_pnl / len(similar_trades)
        win_rate = wins / len(similar_trades)
        avg_sim = sum(t[1] for t in similar_trades) / len(similar_trades)

        logger.info(f"ExpectedEdge: {context.symbol} Exp={expectancy:.2f}% WR={win_rate*100:.1f}% (N={len(similar_trades)})")

        return EdgeProfile(
            expectancy=expectancy,
            win_rate=win_rate,
            sample_size=len(similar_trades),
            similarity_score=avg_sim
        )
