"""Statistical Feature Engine — quantitative features for the Hybrid Intelligence Trading System.

Calculates rolling beta, correlation, volatility, volume analysis, price position,
and funding rate metrics for trade decision context. Results cached in Redis with 1h TTL.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

from loguru import logger


def _to_native(obj):
    """Convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


# Volatility regime thresholds (ATR%)
VOL_LOW_THRESHOLD = 2.0
VOL_HIGH_THRESHOLD = 5.0

# Volume regime thresholds
VOL_SPIKE_LOW = 1.5
VOL_SPIKE_HIGH = 3.0

# Breakout thresholds
BREAKOUT_DISTANCE_PCT = 2.0
BREAKOUT_VOLUME_SPIKE = 1.5

# Overextension threshold (distance from EMA50 %)
OVEREXTENSION_PCT = 10.0

# Cache TTL in seconds
CACHE_TTL = 3600  # 1 hour

# Minimum candles required for feature calculation
MIN_CANDLES = 50


class StatisticalFeatureEngine:
    """Calculates statistical features for trade signals.

    Provides beta vs BTC, rolling correlations, ATR-based volatility,
    volume regime, price position metrics, and funding cost estimates.
    """

    def __init__(self, redis_client: Any = None) -> None:
        """Initialize the feature engine.

        Args:
            redis_client: Optional async Redis client for caching features.
                When None, caching is disabled and features are computed on every call.
        """
        self._redis = redis_client
        logger.debug("StatisticalFeatureEngine.__init__: initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def calculate_features(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        btc_ohlcv: pd.DataFrame,
        funding_rate: float = 0.0,
    ) -> dict:
        """Calculate all statistical features for a symbol.

        Args:
            symbol: Trading pair symbol (e.g. "SOL/USDT").
            ohlcv: DataFrame with columns [open, high, low, close, volume].
            btc_ohlcv: BTC DataFrame for beta/correlation reference.
            funding_rate: Current funding rate as a decimal (e.g. 0.0001 = 0.01%).

        Returns:
            Dict with all feature values. Fields match the spec output structure.
        """
        logger.debug(f"calculate_features: entering symbol={symbol}")

        result: dict = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 — Python 3.9 compat
            "beta_30d": 0.0,
            "correlation_24h": 0.0,
            "correlation_7d": 0.0,
            "atr_pct": 0.0,
            "std_dev_returns": 0.0,
            "volatility_regime": "MEDIUM",
            "volume_spike_ratio": 1.0,
            "volume_trend_slope": 0.0,
            "volume_regime": "NORMAL",
            "distance_from_ema50_pct": 0.0,
            "distance_from_vwap_pct": 0.0,
            "price_vs_ema50": "ABOVE",
            "funding_rate": funding_rate,
            "funding_rate_8h_avg": funding_rate,
            "annualized_funding_cost_pct": round(funding_rate * 3 * 365 * 100, 4),
            "breakout_confirmed": False,
            "overextended": False,
            "volume_confirmed": False,
            "rsi_14": 50.0,
            "adx_14": 0.0,
            "ema_20": 0.0,
            "ema_200": 0.0,
            "sma_20": 0.0,
            "hurst": 0.5,
        }

        if ohlcv is None or btc_ohlcv is None:
            logger.warning(f"calculate_features: null DataFrame(s) for {symbol}")
            return result

        if len(ohlcv) < MIN_CANDLES:
            logger.warning(
                f"calculate_features: insufficient candles for {symbol} "
                f"({len(ohlcv)} < {MIN_CANDLES})"
            )
            return result

        try:
            # Extract close prices and returns
            coin_closes = ohlcv["close"].astype(float)
            coin_returns = coin_closes.pct_change().dropna()

            btc_closes = btc_ohlcv["close"].astype(float)
            btc_returns = btc_closes.pct_change().dropna()

            # Align returns by length (truncate to shorter series)
            n = min(len(coin_returns), len(btc_returns))
            if n > 0:
                coin_r = coin_returns.values[-n:]
                btc_r = btc_returns.values[-n:]
            else:
                coin_r = np.array([], dtype=float)
                btc_r = np.array([], dtype=float)

            # 1. Beta vs BTC (30-day / 720 candles at 1h)
            result["beta_30d"] = self._calculate_beta(coin_r, btc_r)

            # 2. Correlation vs BTC (24h = 24 candles, 7d = 168 candles)
            result["correlation_24h"] = self._calculate_correlation(coin_r, btc_r, 24)
            result["correlation_7d"] = self._calculate_correlation(coin_r, btc_r, 168)

            # 3. Volatility: ATR(14)/Close*100
            highs = ohlcv["high"].astype(float).values
            lows = ohlcv["low"].astype(float).values
            closes = ohlcv["close"].astype(float).values

            atr_val = self._calculate_atr(highs, lows, closes)
            last_close = closes[-1] if len(closes) > 0 else 1.0
            result["atr_pct"] = round((atr_val / last_close * 100) if last_close > 0 else 0.0, 4)

            # Rolling 20-period std dev of returns
            result["std_dev_returns"] = round(
                float(coin_returns.tail(20).std()) if len(coin_returns) >= 2 else 0.0, 6
            )

            result["volatility_regime"] = self.get_volatility_regime(result["atr_pct"])

            # 4. Volume analysis
            volumes = ohlcv["volume"].astype(float).values
            volume_sma = self._sma(volumes, 20)
            last_vol = volumes[-1] if len(volumes) > 0 else 0.0
            result["volume_spike_ratio"] = round(
                (last_vol / volume_sma) if volume_sma > 0 else 1.0, 4
            )
            result["volume_trend_slope"] = round(self._volume_trend_slope(volumes), 6)
            result["volume_regime"] = self._get_volume_regime(result["volume_spike_ratio"])

            # 4b. Technical indicators (RSI, ADX, EMA20/200, SMA20, Hurst)
            closes_list = closes.tolist()
            result["rsi_14"] = round(self._calculate_rsi(closes_list, 14), 4)
            result["adx_14"] = round(self._calculate_adx(highs.tolist(), lows.tolist(), closes_list, 14), 4)
            result["ema_20"] = round(self._ema(closes, 20) or 0.0, 4)
            result["ema_200"] = round(self._ema(closes, 200) or 0.0, 4)
            result["sma_20"] = round(self._sma(closes, 20), 4)
            result["hurst"] = round(self._calculate_hurst(coin_returns.values if len(coin_returns) > 0 else np.array([])), 4)

            # 5. Price position: distance from EMA50, distance from VWAP
            ema50 = self._ema(closes, 50)
            if ema50 is not None and ema50 > 0:
                result["distance_from_ema50_pct"] = round(
                    ((last_close - ema50) / ema50) * 100, 4
                )
                result["price_vs_ema50"] = "ABOVE" if last_close >= ema50 else "BELOW"
            else:
                result["distance_from_ema50_pct"] = 0.0
                result["price_vs_ema50"] = "ABOVE"

            vwap = self._calculate_vwap(
                ohlcv["high"].astype(float).values,
                ohlcv["low"].astype(float).values,
                ohlcv["close"].astype(float).values,
                volumes,
            )
            if vwap is not None and vwap > 0:
                result["distance_from_vwap_pct"] = round(
                    ((last_close - vwap) / vwap) * 100, 4
                )
            else:
                result["distance_from_vwap_pct"] = 0.0

            # 6. Funding rate metrics
            result["funding_rate"] = funding_rate
            result["funding_rate_8h_avg"] = funding_rate
            result["annualized_funding_cost_pct"] = round(funding_rate * 3 * 365 * 100, 4)

            # 7. Signal flags
            result["breakout_confirmed"] = self.is_breakout_confirmed(
                result["volume_spike_ratio"], result["distance_from_ema50_pct"]
            )
            result["overextended"] = abs(result["distance_from_ema50_pct"]) > OVEREXTENSION_PCT
            result["volume_confirmed"] = result["volume_spike_ratio"] >= VOL_SPIKE_LOW

        except Exception as e:
            logger.error(f"calculate_features: error for {symbol}: {e}")

        # Cache to Redis if available
        if self._redis is not None:
            try:
                cache_key = f"karsa:features:{symbol.replace('/', ':')}"
                safe = _to_native(result)
                await self._redis.set(cache_key, json.dumps(safe))
                # TTL is not set via a single call here because the Redis client
                # exposes `set` (no TTL) and `setex` is on the raw redis object.
                # For robustness, use the raw client's setex when available.
                raw = getattr(self._redis, "redis", None)
                if raw is not None:
                    await raw.setex(cache_key, CACHE_TTL, json.dumps(safe))
            except Exception as e:
                logger.debug(f"calculate_features: Redis cache write failed for {symbol}: {e}")

        logger.debug(f"calculate_features: returning features for {symbol}")
        return result

    async def get_features(self, symbol: str) -> dict | None:
        """Get cached features for a symbol from Redis.

        Args:
            symbol: Trading pair symbol (e.g. "SOL/USDT").

        Returns:
            Cached feature dict, or None if not cached / Redis unavailable.
        """
        if self._redis is None:
            return None

        try:
            cache_key = f"karsa:features:{symbol.replace('/', ':')}"
            raw = await self._redis.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"get_features: Redis read failed for {symbol}: {e}")

        return None

    # ------------------------------------------------------------------
    # Volatility & signal classification
    # ------------------------------------------------------------------

    def get_volatility_regime(self, atr_pct: float) -> str:
        """Classify volatility regime based on ATR percentage.

        Args:
            atr_pct: ATR(14) / Close * 100.

        Returns:
            "LOW", "MEDIUM", or "HIGH".
        """
        if atr_pct < VOL_LOW_THRESHOLD:
            return "LOW"
        elif atr_pct > VOL_HIGH_THRESHOLD:
            return "HIGH"
        return "MEDIUM"

    def is_breakout_confirmed(self, volume_spike: float, distance_ema50: float) -> bool:
        """Determine if a breakout is confirmed by volume.

        A breakout is confirmed when price is significantly displaced from EMA50
        and volume spike validates the move.

        Args:
            volume_spike: Volume / SMA(20) ratio.
            distance_ema50: Percentage distance from EMA50 (positive = above).

        Returns:
            True if breakout conditions are met.
        """
        price_displaced = abs(distance_ema50) >= BREAKOUT_DISTANCE_PCT
        volume_validates = volume_spike >= BREAKOUT_VOLUME_SPIKE
        return price_displaced and volume_validates

    # ------------------------------------------------------------------
    # Internal calculation helpers (pure math, no I/O)
    # ------------------------------------------------------------------

    def _calculate_beta(self, coin_returns: np.ndarray, btc_returns: np.ndarray) -> float:
        """Calculate rolling beta: Cov(coin, btc) / Var(btc).

        Args:
            coin_returns: Array of coin returns.
            btc_returns: Array of BTC returns (same length, aligned).

        Returns:
            Beta value. Returns 0.0 when BTC variance is zero.
        """
        if len(coin_returns) < 2 or len(btc_returns) < 2:
            return 0.0

        # Truncate to 30-day window (720 1h candles)
        n = min(len(coin_returns), 720, len(btc_returns))
        coin = coin_returns[-n:]
        btc = btc_returns[-n:]

        btc_var = float(np.var(btc))
        if btc_var == 0.0:
            return 0.0

        cov = float(np.cov(coin, btc)[0, 1])
        return round(cov / btc_var, 4)

    def _calculate_correlation(
        self,
        coin_returns: np.ndarray,
        btc_returns: np.ndarray,
        window: int,
    ) -> float:
        """Calculate Pearson correlation over a rolling window.

        Args:
            coin_returns: Array of coin returns.
            btc_returns: Array of BTC returns (same length, aligned).
            window: Number of periods for correlation.

        Returns:
            Correlation coefficient in [-1, 1]. Returns 0.0 on insufficient data.
        """
        n = min(window, len(coin_returns), len(btc_returns))
        if n < 2:
            return 0.0

        coin = coin_returns[-n:]
        btc = btc_returns[-n:]

        # Check for zero variance (constant series)
        if np.std(coin) == 0.0 or np.std(btc) == 0.0:
            return 0.0

        corr_matrix = np.corrcoef(coin, btc)
        result = float(corr_matrix[0, 1])
        return round(result, 4)

    def _calculate_atr(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> float:
        """Calculate Average True Range using Wilder's smoothing.

        Args:
            highs: Array of high prices.
            lows: Array of low prices.
            closes: Array of close prices.
            period: ATR lookback period (default 14).

        Returns:
            ATR value. Returns 0.0 on insufficient data.
        """
        if len(closes) < period + 1:
            return 0.0

        h = highs.astype(float)
        l = lows.astype(float)
        c = closes.astype(float)

        # True Range series
        tr = np.maximum(
            h[1:] - l[1:],
            np.abs(h[1:] - c[:-1]),
            np.abs(l[1:] - c[:-1]),
        )

        if len(tr) < period:
            return 0.0

        # Wilder's smoothing (EMA-like with period alpha = 1/period)
        atr = float(np.mean(tr[:period]))
        for val in tr[period:]:
            atr = (atr * (period - 1) + float(val)) / period

        return round(atr, 8)

    def _ema(self, data: np.ndarray, period: int) -> float | None:
        """Calculate Exponential Moving Average.

        Args:
            data: Price array.
            period: EMA period.

        Returns:
            Latest EMA value, or None if insufficient data.
        """
        if len(data) < period:
            return None

        k = 2.0 / (period + 1)
        ema = float(data[0])
        for val in data[1:]:
            ema = float(val) * k + ema * (1 - k)
        return ema

    def _sma(self, data: np.ndarray, period: int) -> float:
        """Calculate Simple Moving Average.

        Args:
            data: Price array.
            period: SMA period.

        Returns:
            Latest SMA value. Returns 0.0 on insufficient data.
        """
        if len(data) < period:
            return 0.0
        return float(np.mean(data[-period:]))

    def _calculate_vwap(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
    ) -> float | None:
        """Calculate Volume Weighted Average Price.

        Uses typical price (H+L+C)/3 as the price component.

        Args:
            highs: Array of high prices.
            lows: Array of low prices.
            closes: Array of close prices.
            volumes: Array of volumes.

        Returns:
            VWAP value, or None on zero total volume.
        """
        if len(closes) == 0:
            return None

        typical_price = (highs + lows + closes) / 3.0
        total_vol = float(np.sum(volumes))
        if total_vol == 0:
            return None

        vwap = float(np.sum(typical_price * volumes)) / total_vol
        return vwap

    def _volume_trend_slope(self, volumes: np.ndarray, window: int = 20) -> float:
        """Calculate linear regression slope of volume over a window.

        Args:
            volumes: Array of volume values.
            window: Number of periods for the slope calculation.

        Returns:
            Slope of the best-fit line (positive = increasing volume).
        """
        n = min(window, len(volumes))
        if n < 2:
            return 0.0

        y = volumes[-n:].astype(float)
        x = np.arange(n, dtype=float)

        # Linear regression slope: sum((x - x_mean)(y - y_mean)) / sum((x - x_mean)^2)
        x_mean = float(np.mean(x))
        y_mean = float(np.mean(y))
        ss_xx = float(np.sum((x - x_mean) ** 2))
        if ss_xx == 0:
            return 0.0
        ss_xy = float(np.sum((x - x_mean) * (y - y_mean)))
        return round(ss_xy / ss_xx, 6)

    def _get_volume_regime(self, volume_spike: float) -> str:
        """Classify volume regime.

        Args:
            volume_spike: Volume / SMA(20) ratio.

        Returns:
            "DEPRESSED", "NORMAL", or "ELEVATED".
        """
        if volume_spike < 0.5:
            return "DEPRESSED"
        elif volume_spike >= VOL_SPIKE_HIGH:
            return "ELEVATED"
        return "NORMAL"

    # ------------------------------------------------------------------
    # Technical indicators (RSI, ADX, Hurst)
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_rsi(closes: list[float], period: int = 14) -> float:
        """Relative Strength Index (Wilder's smoothing)."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _calculate_adx(
        highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> float:
        """Average Directional Index."""
        if len(closes) < period + 1:
            return 0.0
        plus_dm = []
        minus_dm = []
        tr_list = []
        for i in range(1, len(closes)):
            h_diff = highs[i] - highs[i - 1]
            l_diff = lows[i - 1] - lows[i]
            plus_dm.append(max(h_diff, 0) if h_diff > l_diff else 0.0)
            minus_dm.append(max(l_diff, 0) if l_diff > h_diff else 0.0)
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        atr = sum(tr_list[:period])
        smooth_plus = sum(plus_dm[:period])
        smooth_minus = sum(minus_dm[:period])
        dx_list = []
        for i in range(period, len(tr_list)):
            atr = atr - atr / period + tr_list[i]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
            if atr == 0:
                continue
            plus_di = 100.0 * smooth_plus / atr
            minus_di = 100.0 * smooth_minus / atr
            di_sum = plus_di + minus_di
            if di_sum == 0:
                continue
            dx_list.append(100.0 * abs(plus_di - minus_di) / di_sum)
        if not dx_list:
            return 0.0
        adx = sum(dx_list) / len(dx_list)
        return adx

    @staticmethod
    def _calculate_hurst(returns: np.ndarray) -> float:
        """Hurst exponent via rescaled range (R/S) analysis."""
        n = len(returns)
        if n < 20:
            return 0.5
        max_k = min(n // 2, 100)
        sizes = []
        rs_values = []
        for k in range(10, max_k + 1, 5):
            n_chunks = n // k
            if n_chunks < 1:
                break
            rs_list = []
            for c in range(n_chunks):
                chunk = returns[c * k : (c + 1) * k]
                mean = float(np.mean(chunk))
                deviations = np.cumsum(chunk - mean)
                r = float(np.max(deviations) - np.min(deviations))
                s = float(np.std(chunk, ddof=1)) if k > 1 else 1.0
                if s > 0:
                    rs_list.append(r / s)
            if rs_list:
                sizes.append(k)
                rs_values.append(float(np.mean(rs_list)))
        if len(sizes) < 2:
            return 0.5
        log_n = np.log(sizes)
        log_rs = np.log(rs_values)
        x_mean = float(np.mean(log_n))
        y_mean = float(np.mean(log_rs))
        ss_xx = float(np.sum((log_n - x_mean) ** 2))
        if ss_xx == 0:
            return 0.5
        ss_xy = float(np.sum((log_n - x_mean) * (log_rs - y_mean)))
        return max(0.0, min(1.0, ss_xy / ss_xx))
