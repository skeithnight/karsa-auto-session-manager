"""Regime Engine — deterministic market regime classifier.

Uses Hurst Exponent (R/S method) + ADX(14) + EMA(200) on BTC 1H candles.
No LLM. Pure Python math.

Classification:
  TREND_BULL:      Hurst > 0.55 AND ADX > 25 AND price > EMA200
  TREND_BEAR:      Hurst > 0.55 AND ADX > 25 AND price < EMA200
  MEAN_REVERSION:  Hurst < 0.45 (anti-persistent)
  CHOP:            ADX < 20 (no directional pressure, default fallback)

Updates every 15 minutes. Stored in Redis system:config:regime.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

from loguru import logger

# Regime states
REGIME_TREND_BULL = "TREND_BULL"
REGIME_TREND_BEAR = "TREND_BEAR"
REGIME_MEAN_REVERSION = "MEAN_REVERSION"
REGIME_CHOP = "CHOP"

# Classification thresholds
HURST_TREND_THRESHOLD = 0.55
HURST_MR_THRESHOLD = 0.45
ADX_TREND_THRESHOLD = 25
ADX_CHOP_THRESHOLD = 20


class RegimeEngine:
    """Classifies market regime from BTC 1H OHLCV data.

    ponytail: all math is pure Python, no numpy/pandas dependency.
    """

    def classify(
        self, ohlcv: list[list], min_candles: int = 200
    ) -> tuple[str, float, float]:
        """Classify market regime from OHLCV candles.

        Args:
            ohlcv: list of [timestamp, open, high, low, close, volume]
            min_candles: minimum candles required (200 for 1H, 50 for 4H)

        Returns:
            Tuple of (regime, hurst, adx) — regime is one of TREND_BULL, TREND_BEAR, MEAN_REVERSION, CHOP.
        """
        logger.debug(f"classify: entering candles={len(ohlcv)}")
        if len(ohlcv) < min_candles:
            logger.warning(
                f"Not enough candles for regime classification: {len(ohlcv)} < {min_candles}"
            )
            logger.debug("classify: returning CHOP (insufficient data)")
            return REGIME_CHOP, 0.0, 0.0

        closes = [float(c[4]) for c in ohlcv]
        highs = [float(c[2]) for c in ohlcv]
        lows = [float(c[3]) for c in ohlcv]

        hurst = self._hurst(closes[-100:])
        adx = self._adx(highs, lows, closes, period=14)
        ema_period = min(200, len(closes) - 1)
        ema200 = self._ema(closes, period=ema_period)
        current_price = closes[-1]

        logger.info(
            f"Regime indicators: hurst={hurst:.4f} adx={adx:.2f} ema200={ema200:.2f} price={current_price:.2f}"
        )

        # Classification logic
        if adx < ADX_CHOP_THRESHOLD:
            regime = REGIME_CHOP
        elif hurst < HURST_MR_THRESHOLD:
            regime = REGIME_MEAN_REVERSION
        elif hurst > HURST_TREND_THRESHOLD and adx > ADX_TREND_THRESHOLD:
            if current_price > ema200:
                regime = REGIME_TREND_BULL
            else:
                regime = REGIME_TREND_BEAR
        else:
            regime = REGIME_CHOP

        logger.info(f"Regime classified: {regime}")
        logger.debug(f"classify: returning {regime}")
        return regime, hurst, adx

    def classify_multi(
        self, ohlcv_1h: list[list], ohlcv_4h: list[list]
    ) -> tuple[str, float, float]:
        """Classify regime requiring BOTH 1H and 4H to agree.

        AND-gate: if either timeframe is CHOP, result is CHOP.
        If they disagree on trend direction, result is CHOP.
        Otherwise, use the 1H regime (higher resolution).
        """
        regime_1h, hurst, adx = self.classify(ohlcv_1h)

        # 4H needs fewer candles (50 ≈ 200/4)
        if len(ohlcv_4h) < 50:
            logger.warning(
                f"Insufficient 4H candles ({len(ohlcv_4h)} < 50), using 1H only"
            )
            return regime_1h, hurst, adx

        regime_4h, _, _ = self.classify(ohlcv_4h, min_candles=50)

        # AND-gate: either CHOP → CHOP
        if regime_1h == REGIME_CHOP or regime_4h == REGIME_CHOP:
            logger.info(f"Multi-TF: CHOP (1H={regime_1h}, 4H={regime_4h})")
            return REGIME_CHOP, hurst, adx

        # Disagree on trend direction → CHOP
        if {regime_1h, regime_4h} == {REGIME_TREND_BULL, REGIME_TREND_BEAR}:
            logger.info(
                f"Multi-TF: trend conflict (1H={regime_1h}, 4H={regime_4h}) → CHOP"
            )
            return REGIME_CHOP, hurst, adx

        # Both agree (or one is MEAN_REVERSION) → use 1H
        logger.info(f"Multi-TF: confirmed {regime_1h} (4H={regime_4h})")
        return regime_1h, hurst, adx

    def _hurst(self, prices: list[float]) -> float:
        """Compute Hurst Exponent using R/S method.

        H > 0.5: trending (persistent)
        H < 0.5: mean-reverting (anti-persistent)
        H ≈ 0.5: random walk
        """
        logger.debug(f"_hurst: entering len={len(prices)}")
        n = len(prices) if len(prices) >= 20 else 20
        if n < 20:
            return 0.5

        rs_values = []
        for window_size in [10, 20, 40]:
            if window_size > n:
                break
            num_windows = n // window_size
            for i in range(num_windows):
                window = prices[i * window_size : (i + 1) * window_size]
                mean = sum(window) / len(window)
                deviations = [(p - mean) for p in window]
                cumulative = []
                s = 0.0
                for d in deviations:
                    s += d
                    cumulative.append(s)
                r = max(cumulative) - min(cumulative)
                s_sq = sum(d**2 for d in deviations) / len(deviations)
                s_std = math.sqrt(s_sq) if s_sq > 0 else 1e-10
                rs_values.append((r / s_std, window_size))

        if not rs_values:
            return 0.5

        # Linear regression on log(R/S) vs log(n)
        log_rs = [math.log(max(rs, 1e-10)) for rs, _ in rs_values]
        log_n = [math.log(float(ns)) for _, ns in rs_values]

        n_pts = len(log_rs)
        if n_pts < 2:
            return 0.5

        sum_x = sum(log_n)
        sum_y = sum(log_rs)
        sum_xy = sum(x * y for x, y in zip(log_n, log_rs))
        sum_x2 = sum(x**2 for x in log_n)

        denom = n_pts * sum_x2 - sum_x**2
        if abs(denom) < 1e-10:
            return 0.5

        hurst = (n_pts * sum_xy - sum_x * sum_y) / denom
        logger.debug(f"_hurst: returning {hurst:.4f}")
        return hurst

    def _adx(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> float:
        """Compute Average Directional Index (ADX)."""
        logger.debug(f"_adx: entering period={period}")
        n = len(closes)
        if n < period + 1:
            return 0.0

        # True Range
        tr_list = []
        plus_dm = []
        minus_dm = []
        for i in range(1, n):
            h_l = highs[i] - lows[i]
            h_pc = abs(highs[i] - closes[i - 1])
            l_pc = abs(lows[i] - closes[i - 1])
            tr_list.append(max(h_l, h_pc, l_pc))

            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)

        if len(tr_list) < period:
            return 0.0

        # Smoothed TR, +DM, -DM (Wilder's smoothing)
        atr = sum(tr_list[:period])
        apdm = sum(plus_dm[:period])
        amdm = sum(minus_dm[:period])

        dx_values = []
        for i in range(period, len(tr_list)):
            atr = atr - atr / period + tr_list[i]
            apdm = apdm - apdm / period + plus_dm[i]
            amdm = amdm - amdm / period + minus_dm[i]

            if atr == 0:
                continue
            plus_di = (apdm / atr) * 100
            minus_di = (amdm / atr) * 100
            di_sum = plus_di + minus_di
            if di_sum == 0:
                continue
            dx = abs(plus_di - minus_di) / di_sum * 100
            dx_values.append(dx)

        if not dx_values:
            return 0.0

        # ADX = smoothed DX
        adx = sum(dx_values[:period]) / period
        for dx in dx_values[period:]:
            adx = (adx * (period - 1) + dx) / period

        logger.debug(f"_adx: returning {adx:.2f}")
        return adx

    def _ema(self, prices: list[float], period: int = 200) -> float:
        """Compute Exponential Moving Average."""
        logger.debug(f"_ema: entering period={period}")
        if len(prices) < period:
            return prices[-1] if prices else 0.0

        multiplier = 2.0 / (period + 1)
        ema = sum(prices[:period]) / period  # SMA for first value
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        logger.debug(f"_ema: returning {ema:.2f}")
        return ema
