"""Portfolio Snapshot (Sprint 6).

Immutable snapshot of portfolio state used by Decision Policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable representation of the portfolio at a specific time."""
    timestamp: float
    equity: Decimal
    cash: Decimal
    positions: dict[str, dict] = field(default_factory=dict)
    sector_exposure: dict[str, Decimal] = field(default_factory=dict)
    symbol_correlation: dict[str, float] = field(default_factory=dict)
    drawdown: Decimal = Decimal("0")
