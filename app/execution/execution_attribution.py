"""Execution Attribution — Track execution quality and drag.

This module tracks execution quality metrics to address
quant trader review recommendation #8:
"Treat execution drag as part of alpha, not a separate concern"

Key metrics tracked:
- Maker vs Taker fill rates
- Slippage (bps) by regime and family
- Edge decay during reprice delay
- Adverse selection cost
- Fill probability by order type

Usage:
    attribution = ExecutionAttribution(redis_client)
    await attribution.record_fill(fill_data)
    report = await attribution.get_attribution_report()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FillRecord:
    """Record of a single fill."""
    symbol: str
    side: str
    order_type: str  # "maker", "taker", "iceberg"
    entry_price: Decimal
    fill_price: Decimal
    amount: Decimal
    slippage_bps: float
    regime: str
    edge_family: str | None = None
    signal_score: float = 0.0
    reprice_attempts: int = 0
    fill_latency_ms: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AttributionMetrics:
    """Aggregated attribution metrics."""
    total_fills: int = 0
    maker_fills: int = 0
    taker_fills: int = 0
    maker_fill_rate: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_maker_slippage: float = 0.0
    avg_taker_slippage: float = 0.0
    adverse_selection_cost: float = 0.0
    edge_decay_per_reprice: float = 0.0
    fill_probability_by_type: dict[str, float] = field(default_factory=dict)
    slippage_by_regime: dict[str, float] = field(default_factory=dict)
    slippage_by_family: dict[str, float] = field(default_factory=dict)


class ExecutionAttribution:
    """Tracks execution quality and attribution.

    This module provides visibility into execution drag,
    enabling the system to:
    1. Compare maker vs taker profitability
    2. Track edge decay during reprice delay
    3. Identify adverse selection patterns
    4. Optimize order routing by regime/family
    """

    def __init__(self, redis_client: Any = None, max_records: int = 1000) -> None:
        """Initialize execution attribution.

        Args:
            redis_client: Redis client for caching metrics.
            max_records: Maximum number of fill records to keep.
        """
        self._redis = redis_client
        self._max_records = max_records
        self._records: list[FillRecord] = []

    async def record_fill(self, fill_data: dict[str, Any]) -> None:
        """Record a fill for attribution.

        Args:
            fill_data: Dict with fill information including:
                - symbol, side, order_type, entry_price, fill_price
                - amount, regime, edge_family, signal_score
                - reprice_attempts, fill_latency_ms
        """
        try:
            entry_price = Decimal(str(fill_data.get("entry_price", 0)))
            fill_price = Decimal(str(fill_data.get("fill_price", 0)))
            amount = Decimal(str(fill_data.get("amount", 0)))

            # Calculate slippage
            if entry_price > 0:
                slippage_bps = float(abs(fill_price - entry_price) / entry_price * 10000)
            else:
                slippage_bps = 0.0

            record = FillRecord(
                symbol=fill_data.get("symbol", ""),
                side=fill_data.get("side", ""),
                order_type=fill_data.get("order_type", "unknown"),
                entry_price=entry_price,
                fill_price=fill_price,
                amount=amount,
                slippage_bps=slippage_bps,
                regime=fill_data.get("regime", "UNKNOWN"),
                edge_family=fill_data.get("edge_family"),
                signal_score=fill_data.get("signal_score", 0.0),
                reprice_attempts=fill_data.get("reprice_attempts", 0),
                fill_latency_ms=fill_data.get("fill_latency_ms", 0),
            )

            self._records.append(record)

            # Trim old records
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

            # Cache in Redis
            if self._redis:
                await self._cache_metrics()

            logger.debug(
                "ExecutionAttribution: recorded %s %s %s fill (slippage=%.1f bps)",
                record.symbol, record.side, record.order_type, record.slippage_bps,
            )

        except Exception as e:
            logger.error("ExecutionAttribution.record_fill failed: %s", e)

    async def get_attribution_report(self) -> AttributionMetrics:
        """Get aggregated attribution metrics.

        Returns:
            AttributionMetrics with aggregated statistics.
        """
        if not self._records:
            return AttributionMetrics()

        # Basic counts
        total = len(self._records)
        maker = [r for r in self._records if r.order_type == "maker"]
        taker = [r for r in self._records if r.order_type == "taker"]

        # Fill rates
        maker_rate = len(maker) / total if total > 0 else 0.0

        # Slippage
        all_slippage = [r.slippage_bps for r in self._records]
        avg_slippage = sum(all_slippage) / len(all_slippage) if all_slippage else 0.0

        maker_slippage = [r.slippage_bps for r in maker]
        avg_maker_slippage = sum(maker_slippage) / len(maker_slippage) if maker_slippage else 0.0

        taker_slippage = [r.slippage_bps for r in taker]
        avg_taker_slippage = sum(taker_slippage) / len(taker_slippage) if taker_slippage else 0.0

        # Adverse selection (taker fills that move against us)
        adverse_cost = 0.0
        for r in taker:
            if (r.side == "buy" and r.fill_price > r.entry_price) or \
               (r.side == "sell" and r.fill_price < r.entry_price):
                adverse_cost += r.slippage_bps
        adverse_cost = adverse_cost / len(taker) if taker else 0.0

        # Edge decay per reprice
        reprice_records = [r for r in self._records if r.reprice_attempts > 0]
        if reprice_records:
            decay_per_reprice = sum(r.slippage_bps for r in reprice_records) / \
                               sum(r.reprice_attempts for r in reprice_records)
        else:
            decay_per_reprice = 0.0

        # Fill probability by type
        fill_prob = {}
        for r in self._records:
            if r.order_type not in fill_prob:
                fill_prob[r.order_type] = 0
            fill_prob[r.order_type] += 1
        fill_prob = {k: v / total for k, v in fill_prob.items()}

        # Slippage by regime
        regime_slippage: dict[str, list[float]] = {}
        for r in self._records:
            if r.regime not in regime_slippage:
                regime_slippage[r.regime] = []
            regime_slippage[r.regime].append(r.slippage_bps)
        slippage_by_regime = {
            k: sum(v) / len(v) for k, v in regime_slippage.items()
        }

        # Slippage by family
        family_slippage: dict[str, list[float]] = {}
        for r in self._records:
            if r.edge_family:
                if r.edge_family not in family_slippage:
                    family_slippage[r.edge_family] = []
                family_slippage[r.edge_family].append(r.slippage_bps)
        slippage_by_family = {
            k: sum(v) / len(v) for k, v in family_slippage.items()
        }

        return AttributionMetrics(
            total_fills=total,
            maker_fills=len(maker),
            taker_fills=len(taker),
            maker_fill_rate=maker_rate,
            avg_slippage_bps=avg_slippage,
            avg_maker_slippage=avg_maker_slippage,
            avg_taker_slippage=avg_taker_slippage,
            adverse_selection_cost=adverse_cost,
            edge_decay_per_reprice=decay_per_reprice,
            fill_probability_by_type=fill_prob,
            slippage_by_regime=slippage_by_regime,
            slippage_by_family=slippage_by_family,
        )

    async def _cache_metrics(self) -> None:
        """Cache metrics in Redis."""
        if not self._redis:
            return

        try:
            import json as _json
            report = await self.get_attribution_report()
            cache_data = {
                "total_fills": report.total_fills,
                "maker_fill_rate": report.maker_fill_rate,
                "avg_slippage_bps": report.avg_slippage_bps,
                "adverse_selection_cost": report.adverse_selection_cost,
                "slippage_by_regime": report.slippage_by_regime,
                "slippage_by_family": report.slippage_by_family,
            }
            await self._redis.set(
                "karsa:execution:attribution",
                _json.dumps(cache_data),
                ex=3600,
            )
        except Exception as e:
            logger.debug("Failed to cache attribution metrics: %s", e)

    def should_use_taker(self, regime: str, urgency: float = 0.5) -> bool:
        """Decide whether to use taker order based on attribution data.

        Args:
            regime: Current market regime.
            urgency: Signal urgency (0.0 to 1.0).

        Returns:
            True if taker order is recommended.
        """
        # High urgency → use taker
        if urgency > 0.8:
            return True

        # Check regime-specific slippage
        report_data = {}
        if self._redis:
            try:
                import json as _json
                raw = self._redis.get("karsa:execution:attribution")
                if raw:
                    report_data = _json.loads(raw)
            except Exception as e:
                logger.warning(f"Failed to read execution attribution from Redis: {type(e).__name__}: {e}")

        slippage_by_regime = report_data.get("slippage_by_regime", {})
        regime_slippage = slippage_by_regime.get(regime, 0.0)

        # If regime has low slippage, taker is fine
        if regime_slippage < 5.0:  # < 5 bps
            return True

        # Otherwise, prefer maker
        return False
