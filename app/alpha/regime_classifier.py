"""Regime Classifier — Phase 6 adaptive market regime detection.

Uses ADX(14) + Hurst Exponent (R/S) + ATR(14) percentile on BTC 1H candles.
Shadow mode: runs alongside existing RegimeEngine, logs comparison.

Decision tree (first match wins):
  1. atr_percentile > 80 AND adx < 20        → CHOP
  2. adx >= 25 AND close > SMA(20)           → TREND_BULL
  3. adx >= 25 AND close <= SMA(20)          → TREND_BEAR
  4. adx < 20 AND hurst < 0.45               → RANGE
  5. fallback                                 → RANGE

Edge cases:
  - < 50 candles → CHOP (insufficient data)
  - all-flat prices → RANGE
  - ADX exactly 25.0 → TREND (inclusive)
"""

from __future__ import annotations

import enum
import json
from typing import TYPE_CHECKING, Any

try:
    import numpy as np
except ImportError:
    np = None

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("karsa.regime_classifier")  # type: ignore[assignment]

if TYPE_CHECKING:
    from app.core.feature_extractor import FeatureVector
    from app.core.market_snapshot import MarketSnapshot

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.1) ---
REGIME_ADX_TREND_THRESHOLD: float = 25.0
REGIME_ADX_CHOP_THRESHOLD: float = 20.0
REGIME_HURST_MR_THRESHOLD: float = 0.45
REGIME_ATR_CHOP_PERCENTILE: float = 80.0
MIN_CANDLES_FOR_CLASSIFICATION: int = 50

# --- Conviction Scaling Constants (cross-ref: docs/plan/big_gainers_secure_plan.md §Phase 1) ---
CONVICTION_ADX_FLOOR: float = 25.0    # ADX below this = 0 conviction for TREND
CONVICTION_ADX_CEILING: float = 40.0  # ADX above this = 1.0 conviction for TREND
CONVICTION_HURST_FLOOR: float = 0.45  # Hurst above this = 0 conviction for RANGE
CONVICTION_HURST_CEILING: float = 0.30  # Hurst below this = 1.0 conviction for RANGE
CONVICTION_CHOP_DEFAULT: float = 0.3  # Fixed conviction for CHOP regime


class MarketRegime(enum.Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    HYPER_BULL = "HYPER_BULL"
    HYPER_BEAR = "HYPER_BEAR"
    RANGE = "RANGE"
    CHOP = "CHOP"
    SNIPER = "SNIPER"
    # Phase 2: Transition states — most profitable moment to trade
    TRANSITION_BULL = "TRANSITION_BULL"  # CHOP/RANGE → TREND_BULL
    TRANSITION_BEAR = "TRANSITION_BEAR"  # CHOP/RANGE → TREND_BEAR

    def encode(self) -> int:
        """Returns integer encoding for ML feature vector."""
        return {
            "RANGE": 0,
            "TREND_BULL": 1,
            "TREND_BEAR": 2,
            "HYPER_BULL": 3,
            "HYPER_BEAR": 4,
            "CHOP": 5,
            "SNIPER": 6,
            "TRANSITION_BULL": 7,
            "TRANSITION_BEAR": 8,
        }.get(self.value, 0)


class RegimeClassifier:
    """Deterministic regime classifier — no LLM, no float for money."""

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client
        self._prev_adx: dict[str, float] = {}  # Phase 2: track ADX for transition detection
        self._adx_history: dict[str, list[float]] = {}  # Rolling window of up to 5 ADX entries per symbol

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self, features: FeatureVector, snapshot: MarketSnapshot
    ) -> MarketRegime:
        """Classify market regime from pre-calculated features.

        Args:
            features: FeatureVector containing technical indicators
            snapshot: MarketSnapshot for current raw prices

        Returns:
            MarketRegime enum value
        """
        if snapshot.candles.shape[0] < MIN_CANDLES_FOR_CLASSIFICATION:
            logger.warning(
                f"RegimeClassifier: only {snapshot.candles.shape[0]} candles (< {MIN_CANDLES_FOR_CLASSIFICATION}), returning CHOP"
            )
            return MarketRegime.CHOP

        closes = snapshot.get_close_prices()

        # Flat-price guard
        if np.all(closes == closes[0]):
            logger.info("RegimeClassifier: all-flat prices, returning RANGE")
            return MarketRegime.RANGE

        adx = features.adx_14 if features.adx_14 is not None else 0.0
        hurst = features.hurst if features.hurst is not None else 0.5
        atr_pct = features.atr_pct if features.atr_pct is not None else 50.0
        sma20 = features.sma_20 or float(closes[-1])
        last_close = float(closes[-1])

        # Phase 2: Get previous ADX for transition detection via 5-period rolling history
        symbol = snapshot.symbol
        if symbol not in self._adx_history:
            self._adx_history[symbol] = []

        adx_history = self._adx_history[symbol]
        adx_prev = adx_history[-1] if adx_history else adx
        adx_history.append(adx)
        if len(adx_history) > 5:
            adx_history.pop(0)

        self._prev_adx[symbol] = adx_prev

        regime = self._decision_tree(adx, hurst, atr_pct, last_close, sma20, adx_prev)

        from app.core import metrics as m

        m.regime_classified_total.labels(regime=regime.value).inc()

        logger.info(
            f"RegimeClassifier: adx={adx:.2f} hurst={hurst:.3f} atr_pct={atr_pct:.0f} "
            f"close={last_close:.2f} sma20={sma20:.2f} → {regime.value}"
        )
        return regime

    def classify_with_conviction(
        self, features: FeatureVector, snapshot: MarketSnapshot
    ) -> tuple[MarketRegime, float]:
        """Classify market regime with conviction score (0.0-1.0).

        Conviction measures how far above/below the regime threshold the indicators are.
        Higher conviction = stronger regime signal = larger position size allowed.

        Args:
            features: FeatureVector containing technical indicators
            snapshot: MarketSnapshot for current raw prices

        Returns:
            Tuple of (MarketRegime, conviction_score) where conviction is 0.0-1.0
        """
        regime = self.classify(features, snapshot)

        adx = features.adx_14 or 0.0
        hurst = features.hurst or 0.5

        # Calculate conviction based on regime type
        if regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
            # TREND conviction: ADX 25 = 0.0, ADX 40 = 1.0 (linear scale)
            conviction = max(0.0, min(1.0,
                (adx - CONVICTION_ADX_FLOOR) / (CONVICTION_ADX_CEILING - CONVICTION_ADX_FLOOR)
            ))
        elif regime in (MarketRegime.HYPER_BULL, MarketRegime.HYPER_BEAR):
            # HYPER regimes always have full conviction (strong trend)
            conviction = 1.0
        elif regime == MarketRegime.RANGE:
            # RANGE conviction: Hurst 0.45 = 0.0, Hurst 0.30 = 1.0 (stronger MR = higher conviction)
            conviction = max(0.0, min(1.0,
                (CONVICTION_HURST_FLOOR - hurst) / (CONVICTION_HURST_FLOOR - CONVICTION_HURST_CEILING)
            ))
        else:  # CHOP
            # CHOP always has low conviction (choppy market = low confidence)
            conviction = CONVICTION_CHOP_DEFAULT

        logger.info(
            f"RegimeClassifier: {regime.value} conviction={conviction:.3f} "
            f"(adx={adx:.2f}, hurst={hurst:.3f})"
        )
        return regime, conviction
        return regime

    async def get_current_regime(self, symbol: str = "BTC/USDT") -> MarketRegime:
        """Read regime from Redis (written by classification loop).
        Checks per-symbol key first, falls back to global BTC regime.
        """
        if self._redis is None:
            logger.warning(
                "RegimeClassifier: no Redis client, returning CHOP (conservative)"
            )
            return MarketRegime.CHOP

        try:
            # Try per-symbol regime first
            symbol_key = f"system:regime:{symbol.replace('/', ':')}"
            raw = await self._redis.get(symbol_key)  # type: ignore[attr-defined]
            if raw is not None:
                raw_s = raw.decode() if isinstance(raw, bytes) else str(raw)
                try:
                    data = json.loads(raw_s)
                    reg_str = data.get("regime", raw_s) if isinstance(data, dict) else raw_s
                    return MarketRegime(reg_str)
                except Exception:
                    return MarketRegime(raw_s)

            # Fallback to global BTC regime
            raw = await self._redis.get("system:config:regime")  # type: ignore[attr-defined]
            if raw is None:
                logger.warning("RegimeClassifier: no regime in Redis, returning RANGE")
                return MarketRegime.RANGE
            raw_s = raw.decode() if isinstance(raw, bytes) else str(raw)
            data = json.loads(raw_s)
            reg_str = data.get("regime", "RANGE") if isinstance(data, dict) else raw_s
            return MarketRegime(reg_str)
        except Exception:
            logger.exception("RegimeClassifier: Redis read failed, returning RANGE")
            return MarketRegime.RANGE

    async def run_classification_loop(
        self,
        ohlcv_fetcher: object | None = None,
        symbol: str = "BTC/USDT",
        interval_seconds: int = 900,
    ) -> None:
        """Background task: classify every interval, write to Redis."""
        import asyncio

        while True:
            try:
                if ohlcv_fetcher is not None:
                    # Fetch BTC 1H candles — convert to numpy array
                    import numpy as _np

                    candles_raw = await ohlcv_fetcher.fetch(symbol, "1h", limit=200)  # type: ignore[attr-defined]
                    candles = (
                        _np.array(candles_raw, dtype=float)
                        if candles_raw
                        else _np.array([])
                    )

                    from app.core.feature_extractor import FeatureExtractor
                    from app.core.feature_store import FeatureStore
                    from app.core.market_snapshot import MarketSnapshot

                    if len(candles) > 0:
                        snapshot = MarketSnapshot(
                            symbol=symbol,
                            timestamp_ms=int(candles[-1][0]),
                            candles=candles
                        )
                        store = FeatureStore(snapshot)
                        features = FeatureExtractor.extract(store)
                        regime, conviction = self.classify_with_conviction(features, snapshot)
                        adx = features.adx_14 or 0.0
                        hurst = features.hurst or 0.5
                        atr_pct = features.atr_pct or 50.0
                    else:
                        regime = MarketRegime.CHOP
                        conviction = CONVICTION_CHOP_DEFAULT
                        adx = 0.0
                        hurst = 0.5
                        atr_pct = 50.0

                    payload = json.dumps(
                        {
                            "regime": regime.value,
                            "conviction": round(conviction, 3),
                            "adx": round(adx, 2),
                            "hurst": round(hurst, 3),
                            "atr_pct": round(atr_pct, 1),
                        }
                    )

                    if self._redis is not None:
                        await self._redis.set("system:config:regime", payload)  # type: ignore[attr-defined]
                        symbol_key = f"system:regime:{symbol.replace('/', ':')}"
                        await self._redis.set(symbol_key, regime.value)
                        logger.info(
                            "🧠 [STAGE 2: REGIME CLASSIFIER] Classified %s ──► %s (Conviction: %.2f, ADX: %.1f, Hurst: %.3f, ATR%%: %.1f)",
                            symbol,
                            regime.value,
                            conviction,
                            adx,
                            hurst,
                            atr_pct,
                        )

                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RegimeClassifier: classification loop error")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Decision tree
    # ------------------------------------------------------------------

    @staticmethod
    def _decision_tree(
        adx: float, hurst: float, atr_pct: float, close: float, sma20: float,
        adx_prev: float = 0.0,
    ) -> MarketRegime:
        """Ordered decision tree — first match wins."""
        # Priority 1: choppy market with high volatility
        if atr_pct > REGIME_ATR_CHOP_PERCENTILE and adx < REGIME_ADX_CHOP_THRESHOLD:
            return MarketRegime.CHOP

        # Priority 1.5: Hyper-Momentum (ADX >= 40)
        if adx >= 40.0:
            if close > sma20:
                return MarketRegime.HYPER_BULL
            else:
                return MarketRegime.HYPER_BEAR

        # Priority 1.8: Phase 2 — Transition & Multi-Resolution Consensus detection
        # CHOP/RANGE → TREND: ADX crossing above 18 with acceleration OR 15m ADX >= 25.0
        adx_acceleration = adx - adx_prev
        if (adx > 18.0 and adx_acceleration > 2.0) or (adx >= 25.0 and hurst < REGIME_HURST_MR_THRESHOLD):
            # ADX rising fast or strong 15m trend momentum in 1H range — multi-resolution transition zone
            if adx < 40.0:
                if close > sma20:
                    return MarketRegime.TRANSITION_BULL
                else:
                    return MarketRegime.TRANSITION_BEAR

        # Priority 2/3: trending (ADX >= 25, inclusive)
        if adx >= REGIME_ADX_TREND_THRESHOLD:
            if close > sma20:
                return MarketRegime.TREND_BULL
            else:
                return MarketRegime.TREND_BEAR

        # Priority 2.5: transitional (ADX 20-25) — treat as weak trend with reduced sizing
        if adx >= REGIME_ADX_CHOP_THRESHOLD:
            if close > sma20:
                return MarketRegime.TREND_BULL  # weak trend bull
            else:
                return MarketRegime.TREND_BEAR  # weak trend bear

        # Priority 4: ranging with anti-persistent price action
        if adx < REGIME_ADX_CHOP_THRESHOLD and hurst < REGIME_HURST_MR_THRESHOLD:
            return MarketRegime.RANGE

        # Priority 5: fallback
        return MarketRegime.RANGE

    # ------------------------------------------------------------------
    # Technical indicator calculations
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_adx(
        highs: np.ndarray[Any, Any],
        lows: np.ndarray[Any, Any],
        closes: np.ndarray[Any, Any],
        period: int = 14,
    ) -> float:
        """ADX(14) via Wilder smoothing. Returns 0.0 on insufficient data."""
        n = len(closes)
        if n < period + 1:
            return 0.0

        # True Range
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        # Directional Movement
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            if up > down and up > 0:
                plus_dm[i] = up
            if down > up and down > 0:
                minus_dm[i] = down

        # Wilder smoothing
        atr = float(np.sum(tr[1 : period + 1]))
        plus_di_smooth = float(np.sum(plus_dm[1 : period + 1]))
        minus_di_smooth = float(np.sum(minus_dm[1 : period + 1]))

        dx_values: list[float] = []

        for i in range(period + 1, n):
            atr = atr - atr / period + tr[i]
            plus_di_smooth = plus_di_smooth - plus_di_smooth / period + plus_dm[i]
            minus_di_smooth = minus_di_smooth - minus_di_smooth / period + minus_dm[i]

            if atr == 0:
                continue

            plus_di = 100.0 * plus_di_smooth / atr
            minus_di = 100.0 * minus_di_smooth / atr
            di_sum = plus_di + minus_di

            if di_sum == 0:
                dx_values.append(0.0)
            else:
                dx_values.append(100.0 * abs(plus_di - minus_di) / di_sum)

        if len(dx_values) < period:
            return 0.0

        # ADX = smoothed DX
        adx = sum(dx_values[:period]) / period
        for dx in dx_values[period:]:
            adx = (adx * (period - 1) + dx) / period

        return adx

    @staticmethod
    def _calculate_hurst(
        prices: np.ndarray[Any, Any], windows: list[int] | None = None
    ) -> float:
        """R/S Hurst exponent. H > 0.5 = trending, H < 0.5 = mean-reverting."""
        if windows is None:
            windows = [8, 16, 32, 64]

        log_prices = np.log(prices[prices > 0])
        if len(log_prices) < max(windows):
            return 0.5  # insufficient data — neutral

        rs_values: list[float] = []
        ns: list[float] = []

        for w in windows:
            if len(log_prices) < w:
                continue

            # Split into non-overlapping windows
            n_chunks = len(log_prices) // w
            if n_chunks < 1:
                continue

            chunk_rs: list[float] = []
            for c in range(n_chunks):
                start = c * w
                end = start + w
                chunk = log_prices[start:end]

                mean_val = float(np.mean(chunk))
                deviations = chunk - mean_val
                cumulative = np.cumsum(deviations)
                R = float(np.max(cumulative) - np.min(cumulative))
                S = float(np.std(chunk, ddof=1))

                if S > 0:
                    chunk_rs.append(R / S)

            if chunk_rs:
                rs_values.append(float(np.mean(chunk_rs)))
                ns.append(float(w))

        if len(rs_values) < 2:
            return 0.5

        # Linear regression on log(R/S) vs log(N)
        log_ns = np.log(ns)
        log_rs = np.log(np.array(rs_values))

        # Slope = Hurst exponent
        n = len(log_ns)
        sum_x = float(np.sum(log_ns))
        sum_y = float(np.sum(log_rs))
        sum_xy = float(np.sum(log_ns * log_rs))
        sum_x2 = float(np.sum(log_ns * log_ns))

        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return 0.5

        hurst = (n * sum_xy - sum_x * sum_y) / denom
        return max(0.0, min(1.0, hurst))

    @staticmethod
    def _calculate_atr_percentile(
        highs: np.ndarray[Any, Any],
        lows: np.ndarray[Any, Any],
        closes: np.ndarray[Any, Any],
        period: int = 14,
    ) -> float:
        """ATR(14) percentile rank vs 100-bar history. Returns 0-100."""
        n = len(closes)
        if n < period + 1:
            return 50.0  # neutral

        # True Range
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        # ATR via Wilder smoothing
        atr_values = np.zeros(n)
        atr_values[period] = float(np.mean(tr[1 : period + 1]))
        for i in range(period + 1, n):
            atr_values[i] = (atr_values[i - 1] * (period - 1) + tr[i]) / period

        current_atr = atr_values[-1]

        # Rank against last 100 ATR values
        lookback = min(100, n - period)
        atr_history = atr_values[-lookback:]
        percentile = float(np.sum(atr_history < current_atr) / len(atr_history) * 100.0)

        return percentile
