"""Strategy Router — Phase 6 regime-aware signal scoring.

Scores signals 0-100 based on market regime. Each regime has its own
scoring sub-strategy. Gate threshold: 65 (neither CHOP component alone passes).

TREND scoring (max 100):
  +30  breakout: price > 20-period high (long) / < 20-period low (short)
  +30  volume surge: current bar > 1.5x 20-bar volume SMA
  +40  global sync: Binance AND OKX confirm same direction

RANGE scoring (max 100):
  +40  BB edge: price pierced Bollinger Band at 2.5 std dev
  +40  wick rejection: candle closed back inside range (pin bar)
  +20  RSI exhaustion: RSI > 75 (shorts) or RSI < 25 (longs)

CHOP scoring (max 100):
  +50  liquidity sweep: order-book delta reversal
  +50  funding extreme: funding rate skewed against trade direction
"""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger

from app.alpha.regime_classifier import MarketRegime

# --- Constants (cross-ref: docs/SYSTEM_CONSTANTS.md §15.2) ---
TREND_SCORE_BREAKOUT: int = 30
TREND_SCORE_VOLUME: int = 30
TREND_SCORE_GLOBAL_SYNC: int = 40

RANGE_SCORE_BB_EDGE: int = 40
RANGE_SCORE_WICK: int = 40
RANGE_SCORE_RSI: int = 20

CHOP_SCORE_LIQUIDITY_SWEEP: int = 50
CHOP_SCORE_FUNDING_EXTREME: int = 50

STRATEGY_GATE_THRESHOLD: int = 65


class StrategyRouter:
    """Regime-aware signal scorer — no LLM, deterministic."""

    def evaluate_signal(
        self,
        candles: np.ndarray[Any, Any] | list[list[float]],
        regime: MarketRegime,
        direction: str,
        global_prices: dict[str, float] | None = None,
        orderbook_delta: float | None = None,
        funding_rate: float | None = None,
    ) -> float:
        """Score a signal 0-100 based on regime and market data.

        Args:
            candles: OHLCV array (N, 6) or list[list]
            regime: current market regime from RegimeClassifier
            direction: "LONG" or "SHORT"
            global_prices: {"binance": price, "okx": price} for cross-exchange sync
            orderbook_delta: net orderbook delta (positive = buy pressure)
            funding_rate: current funding rate

        Returns:
            Score 0-100. Below 65 = reject.
        """
        if not isinstance(candles, np.ndarray):
            candles = np.array(candles, dtype=float)

        if candles.shape[0] < 20:
            logger.warning(
                f"StrategyRouter: only {candles.shape[0]} candles (< 20), returning 0"
            )
            return 0.0

        if regime == MarketRegime.TREND_BULL or regime == MarketRegime.TREND_BEAR:
            score = self._score_trend_strategy(candles, direction, global_prices)
        elif regime == MarketRegime.RANGE:
            score = self._score_range_strategy(candles, direction)
        elif regime == MarketRegime.CHOP:
            score = self._score_chop_strategy(
                candles, direction, orderbook_delta, funding_rate
            )
        else:
            logger.warning(f"StrategyRouter: unknown regime {regime}, returning 0")
            return 0.0

        logger.info(
            f"StrategyRouter: regime={regime.value} dir={direction} score={score}"
        )

        from app.core import metrics as m

        if score < 50:
            bucket = "0-50"
        elif score < 65:
            bucket = "50-65"
        elif score < 85:
            bucket = "65-85"
        else:
            bucket = "85-100"

        m.strategy_scored_total.labels(
            regime=regime.value, score_bucket=bucket
        ).inc()

        return float(score)

    # ------------------------------------------------------------------
    # TREND scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_trend_strategy(
        candles: np.ndarray[Any, Any],
        direction: str,
        global_prices: dict[str, float] | None,
    ) -> int:
        score = 0
        closes = candles[:, 4].astype(float)
        highs = candles[:, 2].astype(float)
        lows = candles[:, 3].astype(float)
        volumes = candles[:, 5].astype(float)

        last_close = closes[-1]

        # Breakout: price > 20-period high (long) / < 20-period low (short)
        rolling_high = float(np.max(highs[-21:-1]))  # exclude current bar
        rolling_low = float(np.min(lows[-21:-1]))

        if direction == "LONG" and last_close > rolling_high:
            score += TREND_SCORE_BREAKOUT
        elif direction == "SHORT" and last_close < rolling_low:
            score += TREND_SCORE_BREAKOUT

        # Volume surge: current > 1.5x 20-bar SMA
        vol_sma = float(np.mean(volumes[-21:-1]))
        if vol_sma > 0 and volumes[-1] > 1.5 * vol_sma:
            score += TREND_SCORE_VOLUME

        # Global sync: Binance AND OKX both above Bybit's price (LONG)
        # or both below (SHORT) — cross-exchange directional agreement
        if global_prices is not None:
            binance_price = global_prices.get("binance")
            okx_price = global_prices.get("okx")
            bybit_price = global_prices.get("bybit", last_close)
            if binance_price is not None and okx_price is not None:
                if (
                    direction == "LONG"
                    and binance_price > bybit_price
                    and okx_price > bybit_price
                ):
                    score += TREND_SCORE_GLOBAL_SYNC
                elif (
                    direction == "SHORT"
                    and binance_price < bybit_price
                    and okx_price < bybit_price
                ):
                    score += TREND_SCORE_GLOBAL_SYNC

        return score

    # ------------------------------------------------------------------
    # RANGE scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_range_strategy(candles: np.ndarray[Any, Any], direction: str) -> int:
        score = 0
        closes = candles[:, 4].astype(float)
        highs = candles[:, 2].astype(float)
        lows = candles[:, 3].astype(float)

        last_close = closes[-1]
        last_high = highs[-1]
        last_low = lows[-1]

        # Bollinger Bands at 2.5 std dev
        sma = float(np.mean(closes[-20:]))
        std = float(np.std(closes[-20:], ddof=1))
        upper_band = sma + 2.5 * std
        lower_band = sma - 2.5 * std

        # BB edge: price pierced band
        if direction == "SHORT" and last_high > upper_band:
            score += RANGE_SCORE_BB_EDGE
        elif direction == "LONG" and last_low < lower_band:
            score += RANGE_SCORE_BB_EDGE

        # Wick rejection: closed back inside bands after piercing
        if direction == "SHORT" and last_high > upper_band and last_close < upper_band:
            score += RANGE_SCORE_WICK
        elif direction == "LONG" and last_low < lower_band and last_close > lower_band:
            score += RANGE_SCORE_WICK

        # RSI exhaustion (14-period, Wilder)
        rsi = StrategyRouter._calculate_rsi(closes, period=14)
        if direction == "SHORT" and rsi > 75:
            score += RANGE_SCORE_RSI
        elif direction == "LONG" and rsi < 25:
            score += RANGE_SCORE_RSI

        return score

    # ------------------------------------------------------------------
    # CHOP scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_chop_strategy(
        candles: np.ndarray[Any, Any],
        direction: str,
        orderbook_delta: float | None,
        funding_rate: float | None,
    ) -> int:
        score = 0

        # Liquidity sweep: orderbook delta reversal
        if orderbook_delta is not None:
            if direction == "LONG" and orderbook_delta < 0:
                score += CHOP_SCORE_LIQUIDITY_SWEEP
            elif direction == "SHORT" and orderbook_delta > 0:
                score += CHOP_SCORE_LIQUIDITY_SWEEP

        # Funding extreme: funding skewed against trade direction
        if funding_rate is not None:
            if direction == "LONG" and funding_rate < -0.0005:
                score += CHOP_SCORE_FUNDING_EXTREME
            elif direction == "SHORT" and funding_rate > 0.0005:
                score += CHOP_SCORE_FUNDING_EXTREME

        return score

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_rsi(closes: np.ndarray[Any, Any], period: int = 14) -> float:
        """RSI via Wilder smoothing. Returns 50.0 on insufficient data."""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)
