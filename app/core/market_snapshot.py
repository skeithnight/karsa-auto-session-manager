"""Market Snapshot (Sprint 1).

Immutable representation of raw market state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np


@dataclass(frozen=True)
class MarketSnapshot:
    """Immutable snapshot of the market at a specific point in time."""
    symbol: str
    timestamp_ms: int
    candles: np.ndarray  # [ts, open, high, low, close, volume]

    # Global state features
    global_vwap: Decimal | None = None
    global_skew: float | None = None
    funding_rate: float | None = None
    oi_change: float | None = None
    orderbook_delta: float | None = None
    cvd_slope: float | None = None
    liquidity_walls: dict[str, float | None] | None = None

    # Cross-exchange
    global_prices: dict[str, float] | None = None

    # Raw liquidity/spread
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None

    def get_close_prices(self) -> np.ndarray:
        return self.candles[:, 4].astype(float)

    def get_high_prices(self) -> np.ndarray:
        return self.candles[:, 2].astype(float)

    def get_low_prices(self) -> np.ndarray:
        return self.candles[:, 3].astype(float)

    def get_volumes(self) -> np.ndarray:
        return self.candles[:, 5].astype(float)
