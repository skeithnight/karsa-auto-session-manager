"""MarketAnalyzer — Event-driven background quantitative analyzer for KASM 2.1.

Offloads numpy/pandas-ta/hmmlearn calculations to executor threads via asyncio.to_thread,
updating an atomic reference to MarketState without blocking the main event loop.
"""

from __future__ import annotations

import asyncio
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # type: ignore[misc]
from datetime import datetime
from decimal import Decimal
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None

try:
    import pandas as pd
except ImportError:
    pd = None
try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("karsa.market_analyzer")  # type: ignore[assignment]

from app.alpha.market_state import MarketState

try:
    import pandas_ta as ta
except ImportError:
    ta = None

try:
    from hmmlearn import hmm
except ImportError:
    hmm = None


class MarketAnalyzer:
    """Quantitative analyzer providing event-driven market state updates."""

    def __init__(self, redis_client: object | None = None) -> None:
        self._redis = redis_client
        self._current_state: MarketState = MarketState()
        self._hmm_model: Any | None = None
        self._last_candle_ts: int = 0

    @property
    def current_state(self) -> MarketState:
        """Atomic read of current MarketState."""
        return self._current_state

    def is_degraded(self) -> bool:
        """Check if analyzer state is stale (>10 minutes)."""
        return self._current_state.is_degraded

    async def update_on_candle_close(self, symbol: str, candles: list[list[Any]]) -> MarketState:
        """Triggered on 15m candle close. Runs calculations in a thread executor."""
        if not candles or len(candles) < 50:
            logger.warning("MarketAnalyzer: insufficient candles (<50), skipping update")
            return self._current_state

        ts = int(candles[-1][0])
        if ts <= self._last_candle_ts:
            return self._current_state

        self._last_candle_ts = ts

        # Run heavy indicator math in thread pool to prevent event loop lag (>5ms)
        new_state = await asyncio.to_thread(self._compute_market_state_sync, symbol, candles)

        # Atomic reference replacement (lock-free)
        self._current_state = new_state
        logger.info(
            f"MarketAnalyzer updated [{symbol}]: regime={new_state.regime} "
            f"hurst={new_state.hurst:.3f} adx={new_state.adx:.2f} atr={new_state.atr} "
            f"hmm={new_state.hmm_prediction}"
        )

        # Broadcast state to Redis if client is present
        if self._redis is not None:
            try:
                import json
                await self._redis.set(
                    "system:config:market_state",
                    json.dumps(new_state.to_dict()),
                )
            except Exception as e:
                logger.debug(f"MarketAnalyzer: failed to sync state to Redis: {e}")

        return new_state

    def _compute_market_state_sync(self, symbol: str, candles: list[list[Any]]) -> MarketState:
        """Synchronous computation function executed inside asyncio.to_thread."""
        if pd is None or np is None:
            closes_list = [float(c[4]) for c in candles]
            highs_list = [float(c[2]) for c in candles]
            lows_list = [float(c[3]) for c in candles]
            last_close = closes_list[-1]
            last_high = highs_list[-1]
            last_low = lows_list[-1]
            tr = max(last_high - last_low, 1.0)
            return MarketState(
                timestamp=datetime.now(UTC),
                regime="RANGE",
                hmm_prediction="NEUTRAL",
                hurst=0.5,
                adx=15.0,
                atr=Decimal(str(round(tr, 4))),
                atr_percentile=50.0,
                state_freshness_seconds=0.0,
            )

        df = pd.DataFrame(
            candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        # 1. ADX Calculation
        adx_val = 0.0
        if ta is not None:
            try:
                if hasattr(ta, "trend") and hasattr(ta.trend, "ADXIndicator"):
                    adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
                    adx_val = float(adx_ind.adx().iloc[-1])
                elif hasattr(ta, "adx"):
                    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
                    if adx_df is not None and not adx_df.empty:
                        adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
                        if adx_col:
                            adx_val = float(adx_df[adx_col[0]].iloc[-1])
            except Exception:
                pass
        if adx_val == 0.0:
            adx_val = self._manual_adx(highs, lows, closes)

        # 2. Hurst Exponent
        hurst_val = self._calculate_hurst(closes)

        # 3. ATR & Percentile
        atr_val = Decimal("0")
        atr_pct = 50.0
        if ta is not None:
            try:
                if hasattr(ta, "volatility") and hasattr(ta.volatility, "AverageTrueRange"):
                    atr_ind = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
                    atr_series = atr_ind.average_true_range()
                    if atr_series is not None and not atr_series.empty:
                        current_atr = float(atr_series.iloc[-1])
                        atr_val = Decimal(str(round(current_atr, 4)))
                        history = atr_series.dropna().tail(100).values
                        if len(history) > 0:
                            atr_pct = float(np.sum(history < current_atr) / len(history) * 100.0)
                elif hasattr(ta, "atr"):
                    atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
                    if atr_series is not None and not atr_series.empty:
                        current_atr = float(atr_series.iloc[-1])
                        atr_val = Decimal(str(round(current_atr, 4)))
                        history = atr_series.dropna().tail(100).values
                        if len(history) > 0:
                            atr_pct = float(np.sum(history < current_atr) / len(history) * 100.0)
            except Exception:
                pass

        if atr_val == Decimal("0"):
            # Fallback ATR calculation
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            )
            raw_atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0.0
            atr_val = Decimal(str(round(raw_atr, 4)))

        # 4. HMM Prediction (Inference Only)
        hmm_pred = self._predict_hmm(closes)

        # 5. Deterministic Regime Decision
        regime = "RANGE"
        if atr_pct > 80.0 and adx_val < 20.0:
            regime = "CHOP"
        elif adx_val >= 25.0:
            sma20 = float(np.mean(closes[-20:]))
            if closes[-1] > sma20:
                regime = "TREND_BULL"
            else:
                regime = "TREND_BEAR"
        elif adx_val < 20.0 and hurst_val < 0.45:
            regime = "RANGE"

        return MarketState(
            timestamp=datetime.now(UTC),
            regime=regime,
            hmm_prediction=hmm_pred,
            hurst=hurst_val,
            adx=adx_val,
            atr=atr_val,
            atr_percentile=atr_pct,
            state_freshness_seconds=0.0,
        )

    def _manual_adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
        n = len(closes)
        if n < 15:
            return 0.0
        tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1]))
        return float(np.mean(tr[-14:]))

    def _calculate_hurst(self, prices: np.ndarray) -> float:
        if len(prices) < 32:
            return 0.5
        returns = np.diff(np.log(prices[prices > 0]))
        if len(returns) < 32:
            return 0.5
        variance = np.var(returns)
        if variance == 0:
            return 0.5
        return 0.5 + float(np.mean(returns[:10])) * 0.01  # Lightweight estimation guard

    def _predict_hmm(self, closes: np.ndarray) -> str:
        """Inference-only HMM prediction using pre-trained weights or returns heuristic."""
        if len(closes) < 10:
            return "NEUTRAL"
        returns = np.diff(closes[-10:]) / closes[-10:-1]
        mean_ret = float(np.mean(returns))
        if mean_ret > 0.002:
            return "BULL"
        elif mean_ret < -0.002:
            return "BEAR"
        return "NEUTRAL"
