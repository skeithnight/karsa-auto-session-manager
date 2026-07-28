"""Edge Families — Modular strategy evaluation components.

Each edge family encapsulates:
- Its own entry logic
- Its own regime requirements
- Its own filter set
- Its own expected holding period
- Its own stop/target behavior

Families are evaluated independently by the ScoreComposer.
"""

from app.consumer.edge_families.base import EdgeFamily, EdgeFamilyResult
from app.consumer.edge_families.trend import TrendContinuation
from app.consumer.edge_families.mean_reversion import MeanReversion
from app.consumer.edge_families.carry import CarryDislocation
from app.consumer.edge_families.liquidation_squeeze import LiquidationSqueeze
from app.consumer.edge_families.event_breakout import EventBreakout

__all__ = [
    "EdgeFamily",
    "EdgeFamilyResult",
    "TrendContinuation",
    "MeanReversion",
    "CarryDislocation",
    "LiquidationSqueeze",
    "EventBreakout",
]
