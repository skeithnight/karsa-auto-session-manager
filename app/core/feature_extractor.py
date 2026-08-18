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
    cvd_slope: float | None = None
    spread_pct: float | None = None

    # Pre-calculated Qualities (added in Sprint 2)
    market_quality_score: float | None = None
    candle_quality_score: float | None = None
    noise_score: float | None = None
    liquidity_score: float | None = None

    # Cross-Asset, Volume & Statistical Extension
    beta_30d: float | None = None
    correlation_24h: float | None = None
    volume_spike_ratio: float | None = None
    distance_from_ema50_pct: float | None = None
    breakout_confirmed: bool | None = None
    annualized_funding_cost_pct: float | None = None
    ev_score: float | None = None


class FeatureExtractor:
    """Extracts a standard FeatureVector from a FeatureStore."""

    @staticmethod
    def extract(store: FeatureStore) -> FeatureVector:
        """Build the FeatureVector using cached values from the FeatureStore."""
        import logging
        _logger = logging.getLogger("karsa.feature_extractor")

        closes = store.snapshot.get_close_prices()
        fv = FeatureVector(
            close=float(closes[-1]) if len(closes) > 0 else None,
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
            cvd_slope=store.snapshot.cvd_slope,
            spread_pct=store.get_spread_pct()
        )

        # Log which fields are None for debugging
        none_fields = [k for k, v in fv.__dict__.items() if v is None]
        if none_fields:
            _logger.debug(
                "FeatureExtractor: %d fields are None: %s (candles=%d)",
                len(none_fields), none_fields, len(closes),
            )
        return fv
