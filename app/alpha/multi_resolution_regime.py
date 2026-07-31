"""Multi-Resolution Regime — compute regime at 15m/1H/4H for holding-period matching.

Phase 2 (Regime Revolution): Different strategies need different timeframe regimes.
- CHOP scalps (15min hold) → use 15m regime
- RANGE mean-reversion (4h hold) → use 1H regime
- TREND following (24h hold) → use 4H regime

This module fetches and caches regime at multiple resolutions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier

logger = logging.getLogger(__name__)

# Strategy → timeframe mapping
STRATEGY_TIMEFRAME = {
    "CHOP_SWEEP": "15m",
    "CHOP_MEAN_REVERT": "15m",
    "CHOP_CARRY": "1h",
    "RANGE": "1h",
    "TREND_BULL": "4h",
    "TREND_BEAR": "4h",
    "TRANSITION_BULL": "4h",
    "TRANSITION_BEAR": "4h",
    "HYPER_BULL": "15m",
    "HYPER_BEAR": "15m",
}


class MultiResolutionRegime:
    """Compute and cache regime at multiple timeframes.

    Usage:
        mrr = MultiResolutionRegime(classifier)
        regimes = await mrr.classify_all(symbol, ohlcv_fetcher)
        regime = mrr.get_regime_for_strategy(regimes, "CHOP_SWEEP")
    """

    def __init__(self, classifier: RegimeClassifier | None = None) -> None:
        self._classifier = classifier or RegimeClassifier()
        self._cache: dict[str, dict[str, MarketRegime]] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_ttl = 300  # 5 minutes

    async def classify_all(
        self,
        symbol: str,
        ohlcv_fetcher: object | None = None,
    ) -> dict[str, MarketRegime]:
        """Classify regime at 15m, 1H, 4H timeframes.

        Returns:
            Dict mapping timeframe to MarketRegime.
        """
        now = datetime.now(timezone.utc).timestamp()

        # Check cache
        if symbol in self._cache_ts and (now - self._cache_ts[symbol]) < self._cache_ttl:
            return self._cache.get(symbol, {})

        if ohlcv_fetcher is None:
            logger.debug("MultiResolutionRegime: no fetcher, returning empty")
            return {}

        regimes: dict[str, MarketRegime] = {}

        for tf in ("15m", "1h", "4h"):
            try:
                candles_raw = await ohlcv_fetcher.fetch(symbol, tf, limit=200)  # type: ignore[attr-defined]
                if not candles_raw or len(candles_raw) < 50:
                    regimes[tf] = MarketRegime.CHOP
                    continue

                if np is not None:
                    candles = np.array(candles_raw, dtype=float)
                else:
                    import numpy as _np
                    candles = _np.array(candles_raw, dtype=float)

                from app.core.feature_extractor import FeatureExtractor
                from app.core.feature_store import FeatureStore
                from app.core.market_snapshot import MarketSnapshot

                snapshot = MarketSnapshot(
                    symbol=symbol,
                    timestamp_ms=int(candles[-1][0]),
                    candles=candles,
                )
                store = FeatureStore(snapshot)
                features = FeatureExtractor.extract(store)
                regime = self._classifier.classify(features, snapshot)
                regimes[tf] = regime

            except Exception as e:
                logger.debug("MultiResolutionRegime: %s %s failed: %s", symbol, tf, e)
                regimes[tf] = MarketRegime.CHOP

        # Cache
        self._cache[symbol] = regimes
        self._cache_ts[symbol] = now

        logger.debug("MultiResolutionRegime: %s regimes=%s", symbol, regimes)
        return regimes

    def get_regime_for_strategy(
        self,
        regimes: dict[str, MarketRegime],
        strategy: str,
    ) -> MarketRegime:
        """Match regime resolution to strategy holding period."""
        tf = STRATEGY_TIMEFRAME.get(strategy, "1h")
        return regimes.get(tf, MarketRegime.CHOP)
