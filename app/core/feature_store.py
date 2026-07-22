"""Feature Store (Sprint 1).

Calculates indicators exactly once and caches them.
Never contains business logic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np

from app.core.market_snapshot import MarketSnapshot


class FeatureStore:
    """Calculates and caches technical indicators for a given MarketSnapshot."""

    def __init__(self, snapshot: MarketSnapshot):
        self.snapshot = snapshot
        self._cache: dict[str, Any] = {}

    def get_ema(self, period: int) -> float | None:
        key = f"ema_{period}"
        if key not in self._cache:
            closes = self.snapshot.get_close_prices()
            if len(closes) < period:
                self._cache[key] = None
            else:
                from app.alpha.ta_tools import calculate_ema
                ema_dec = calculate_ema([Decimal(str(c)) for c in closes], period=period)
                self._cache[key] = float(ema_dec) if ema_dec else None
        return self._cache[key]

    def get_sma(self, period: int) -> float | None:
        key = f"sma_{period}"
        if key not in self._cache:
            closes = self.snapshot.get_close_prices()
            if len(closes) < period:
                self._cache[key] = None
            else:
                self._cache[key] = float(np.mean(closes[-period:]))
        return self._cache[key]

    def get_atr(self, period: int = 14) -> float | None:
        key = f"atr_{period}"
        if key not in self._cache:
            highs = self.snapshot.get_high_prices()
            lows = self.snapshot.get_low_prices()
            closes = self.snapshot.get_close_prices()

            if len(closes) < period + 1:
                self._cache[key] = None
            else:
                tr = np.maximum(
                    highs[1:] - lows[1:],
                    np.maximum(
                        np.abs(highs[1:] - closes[:-1]),
                        np.abs(lows[1:] - closes[:-1])
                    )
                )
                atr = float(np.mean(tr[:period]))
                for i in range(period, len(tr)):
                    atr = (atr * (period - 1) + float(tr[i])) / period
                self._cache[key] = atr
        return self._cache[key]

    def get_atr_percentile(self, period: int = 14) -> float | None:
        key = f"atr_pct_{period}"
        if key not in self._cache:
            from app.alpha.regime_classifier import RegimeClassifier
            pct = RegimeClassifier._calculate_atr_percentile(
                self.snapshot.get_high_prices(),
                self.snapshot.get_low_prices(),
                self.snapshot.get_close_prices(),
                period=period
            )
            self._cache[key] = float(pct)
        return self._cache[key]

    def get_rsi(self, period: int = 14) -> float | None:
        key = f"rsi_{period}"
        if key not in self._cache:
            closes = self.snapshot.get_close_prices()
            if len(closes) < period + 1:
                self._cache[key] = None
            else:
                from app.alpha.strategy_router import StrategyRouter
                self._cache[key] = StrategyRouter._calculate_rsi(closes, period=period)
        return self._cache[key]

    def get_adx(self, period: int = 14) -> float | None:
        key = f"adx_{period}"
        if key not in self._cache:
            from app.alpha.regime_classifier import RegimeClassifier
            adx = RegimeClassifier._calculate_adx(
                self.snapshot.get_high_prices(),
                self.snapshot.get_low_prices(),
                self.snapshot.get_close_prices(),
                period=period
            )
            self._cache[key] = float(adx)
        return self._cache[key]

    def get_hurst(self) -> float | None:
        key = "hurst"
        if key not in self._cache:
            from app.alpha.regime_classifier import RegimeClassifier
            hurst = RegimeClassifier._calculate_hurst(self.snapshot.get_close_prices())
            self._cache[key] = float(hurst)
        return self._cache[key]

    def get_spread_pct(self) -> float | None:
        key = "spread_pct"
        if key not in self._cache:
            if self.snapshot.best_bid and self.snapshot.best_ask and self.snapshot.best_bid > 0:
                spread = (self.snapshot.best_ask - self.snapshot.best_bid) / self.snapshot.best_bid
                self._cache[key] = float(spread * 100)
            else:
                self._cache[key] = None
        return self._cache[key]
