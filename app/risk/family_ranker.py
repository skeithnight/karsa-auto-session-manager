"""Family-Aware Ranker — Capital allocation by edge family.

This module provides family-aware ranking for portfolio construction,
addressing quant trader review recommendation #7:
"Tighten portfolio logic around correlation and capital competition"

Key concepts:
- Each edge family has its own expected return per unit risk
- Capital should flow to the best risk-adjusted families
- Correlation within families is higher than across families
- Family-aware ranking prevents over-concentration in one style

Usage:
    ranker = FamilyRanker(redis_client)
    rankings = await ranker.rank_families()
    allocation = ranker.allocate_capital(rankings, total_equity)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FamilyMetrics:
    """Metrics for a single edge family."""
    family_name: str
    trade_count: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_holding_minutes: float = 0.0
    expectancy: float = 0.0
    risk_adjusted_return: float = 0.0


@dataclass
class FamilyAllocation:
    """Capital allocation for a family."""
    family_name: str
    allocation_pct: float
    allocation_usd: Decimal
    max_positions: int
    confidence: float


class FamilyRanker:
    """Ranks edge families by risk-adjusted return.

    This replaces the generic "confidence" ranking with
    family-aware capital allocation.
    """

    # Maximum allocation per family (prevent over-concentration)
    MAX_FAMILY_ALLOCATION = 0.40  # 40% max
    MIN_FAMILY_ALLOCATION = 0.05  # 5% min

    # Minimum trades required for ranking
    MIN_TRADES_FOR_RANKING = 10

    def __init__(self, redis_client: Any = None) -> None:
        """Initialize the family ranker.

        Args:
            redis_client: Redis client for caching rankings.
        """
        self._redis = redis_client

    async def get_family_metrics(
        self,
        trade_store: Any,
        family_name: str,
    ) -> FamilyMetrics:
        """Calculate metrics for a specific family.

        Args:
            trade_store: TradeStore instance.
            family_name: Name of the edge family.

        Returns:
            FamilyMetrics with computed metrics.
        """
        try:
            # Get trades for this family
            trades = await trade_store.get_recent_trades(limit=200)
            family_trades = [
                t for t in trades
                if t.get("edge_family") == family_name
            ]

            if len(family_trades) < self.MIN_TRADES_FOR_RANKING:
                return FamilyMetrics(family_name=family_name)

            # Calculate metrics
            pnls = [float(t.get("pnl_pct", 0)) for t in family_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            win_rate = len(wins) / len(pnls) if pnls else 0.0
            avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

            # Sharpe ratio (simplified)
            import numpy as np
            if len(pnls) > 1:
                sharpe = np.mean(pnls) / max(0.001, np.std(pnls))
            else:
                sharpe = 0.0

            # Max drawdown
            cumulative = np.cumsum(pnls)
            running_max = np.maximum.accumulate(cumulative)
            drawdowns = running_max - cumulative
            max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

            # Holding time
            holding_times = []
            for t in family_trades:
                ht = t.get("holding_time_minutes")
                if ht is not None:
                    holding_times.append(float(ht))
            avg_holding = np.mean(holding_times) if holding_times else 0.0

            # Expectancy = P(win) * avg_win - P(loss) * avg_loss
            avg_win = np.mean(wins) if wins else 0.0
            avg_loss = abs(np.mean(losses)) if losses else 0.0
            expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

            # Risk-adjusted return (return / drawdown)
            risk_adj = avg_pnl / max(0.001, max_dd) if max_dd > 0 else avg_pnl

            return FamilyMetrics(
                family_name=family_name,
                trade_count=len(family_trades),
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                sharpe_ratio=float(sharpe),
                max_drawdown=max_dd,
                avg_holding_minutes=float(avg_holding),
                expectancy=expectancy,
                risk_adjusted_return=risk_adj,
            )

        except Exception as e:
            logger.error("get_family_metrics failed for %s: %s", family_name, e)
            return FamilyMetrics(family_name=family_name)

    async def rank_families(
        self,
        trade_store: Any,
        families: list[str] | None = None,
    ) -> list[FamilyMetrics]:
        """Rank all families by risk-adjusted return.

        Args:
            trade_store: TradeStore instance.
            families: List of family names to rank (default: all).

        Returns:
            List of FamilyMetrics sorted by risk-adjusted return.
        """
        if families is None:
            families = [
                "trend_continuation",
                "mean_reversion",
                "carry_dislocation",
                "liquidation_squeeze",
                "event_breakout",
            ]

        metrics = []
        for family in families:
            m = await self.get_family_metrics(trade_store, family)
            if m.trade_count >= self.MIN_TRADES_FOR_RANKING:
                metrics.append(m)

        # Sort by risk-adjusted return (descending)
        metrics.sort(key=lambda x: x.risk_adjusted_return, reverse=True)

        # Cache in Redis if available
        if self._redis:
            try:
                import json as _json
                cache_data = [
                    {
                        "family": m.family_name,
                        "risk_adj_return": m.risk_adjusted_return,
                        "expectancy": m.expectancy,
                        "win_rate": m.win_rate,
                        "trade_count": m.trade_count,
                    }
                    for m in metrics
                ]
                await self._redis.set(
                    "karsa:family:ranking",
                    _json.dumps(cache_data),
                    ex=3600,  # 1 hour TTL
                )
            except Exception as e:
                logger.debug("Failed to cache family ranking: %s", e)

        return metrics

    def allocate_capital(
        self,
        ranked_families: list[FamilyMetrics],
        total_equity: Decimal,
        max_families: int = 3,
    ) -> list[FamilyAllocation]:
        """Allocate capital across top families.

        Args:
            ranked_families: Families sorted by risk-adjusted return.
            total_equity: Total portfolio equity.
            max_families: Maximum number of families to allocate to.

        Returns:
            List of FamilyAllocation for top families.
        """
        if not ranked_families:
            return []

        # Take top N families
        top_families = ranked_families[:max_families]

        # Calculate allocation based on risk-adjusted return
        total_risk_adj = sum(max(0.001, f.risk_adjusted_return) for f in top_families)

        allocations = []
        remaining_equity = total_equity

        for i, family in enumerate(top_families):
            # Proportional allocation by risk-adjusted return
            risk_adj = max(0.001, family.risk_adjusted_return)
            raw_pct = risk_adj / total_risk_adj

            # Apply min/max bounds
            if i == len(top_families) - 1:
                # Last family gets remainder
                allocation_pct = max(self.MIN_FAMILY_ALLOCATION,
                                    min(self.MAX_FAMILY_ALLOCATION, 1.0 - sum(a.allocation_pct for a in allocations)))
            else:
                allocation_pct = max(self.MIN_FAMILY_ALLOCATION,
                                    min(self.MAX_FAMILY_ALLOCATION, raw_pct))

            allocation_usd = total_equity * Decimal(str(allocation_pct))
            remaining_equity -= allocation_usd

            # Max positions per family (based on trade count)
            max_positions = max(1, min(3, family.trade_count // 10))

            allocations.append(FamilyAllocation(
                family_name=family.family_name,
                allocation_pct=allocation_pct,
                allocation_usd=allocation_usd,
                max_positions=max_positions,
                confidence=family.expectancy,
            ))

        logger.info(
            "Family allocation: %s",
            [(a.family_name, f"{a.allocation_pct:.1%}") for a in allocations],
        )

        return allocations
