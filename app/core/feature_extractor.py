"""Feature Extractor and Feature Vector (Sprint 1).

Produces a standardized Feature Vector from the Feature Store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.feature_store import FeatureStore


@dataclass(frozen=True)
class FeatureVector:
    """Standardized representation of all calculated features."""
    # Technicals
    close: float | None = None
    ema_20: float | None = None
    ema_200: float | None = None
    sma_20: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    rsi_14: float | None = None
    adx_14: float | None = None
    hurst: float | None = None

    # Derivatives
    funding_rate: float | None = None
    oi_change: float | None = None

    # Microstructure
    orderbook_delta: float | None = None
    spread_pct: float | None = None

    # Pre-calculated Qualities (added in Sprint 2)
    market_quality_score: float | None = None
    candle_quality_score: float | None = None
    noise_score: float | None = None
    liquidity_score: float | None = None


class FeatureExtractor:
    """Extracts a standard FeatureVector from a FeatureStore."""

    @staticmethod
    def extract(store: FeatureStore) -> FeatureVector:
        """Build the FeatureVector using cached values from the FeatureStore."""
        return FeatureVector(
            close=float(store.snapshot.get_close_prices()[-1]) if len(store.snapshot.get_close_prices()) > 0 else None,
            ema_20=store.get_ema(20),
            ema_200=store.get_ema(200),
            sma_20=store.get_sma(20),
            atr=store.get_atr(),
            atr_pct=store.get_atr_percentile(),
            rsi_14=store.get_rsi(14),
            adx_14=store.get_adx(14),
            hurst=store.get_hurst(),
            funding_rate=store.snapshot.funding_rate,
            oi_change=store.snapshot.oi_change,
            orderbook_delta=store.snapshot.orderbook_delta,
            spread_pct=store.get_spread_pct()
        )
