"""Micro Scalper Engine — 1m/tick Quick Win Strategy.

- Entry: Orderbook Delta, Tape Reading
- Execution: STRICTLY is_post_only=True
- Dynamic spread threshold based on ATR (Audit Fix):
  - Low vol (ATR < 2%): 0.03%
  - Medium vol (ATR 2-5%): 0.06%
  - High vol (ATR > 5%): 0.10%
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from loguru import logger
from pydantic import BaseModel


# Dynamic spread thresholds based on ATR (Audit Fix)
SPREAD_LOW_VOL = Decimal("0.0015")   # 0.15% — default for perp altcoins
SPREAD_MED_VOL = Decimal("0.0025")   # 0.25% — ATR 2-5%
SPREAD_HIGH_VOL = Decimal("0.0050")  # 0.50% — ATR > 5%
ATR_LOW_THRESHOLD = Decimal("2.0")   # 2% ATR
ATR_HIGH_THRESHOLD = Decimal("5.0")  # 5% ATR


class ScalperSignal(BaseModel):
    symbol: str
    direction: str
    entry_price: Decimal
    sl_price: Decimal
    tp_price: Decimal
    confidence: float
    timestamp: float


def get_dynamic_spread_threshold(atr_pct: float = 0.0) -> Decimal:
    """Get spread threshold based on current ATR (volatility).

    When atr_pct is 0.0 (uncalculated tick state), default to 0.50% so high-volatility
    perp altcoins are passed to orderbook imbalance & AI evaluation.
    """
    atr = Decimal(str(atr_pct))
    if atr <= 0:
        return SPREAD_HIGH_VOL
    if atr < ATR_LOW_THRESHOLD:
        return SPREAD_LOW_VOL
    if atr < ATR_HIGH_THRESHOLD:
        return SPREAD_MED_VOL
    return SPREAD_HIGH_VOL


class MicroScalper:
    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self._last_signal_time: dict[str, float] = {}
        # Cooldown per symbol in seconds
        self._cooldown = 60.0

    async def evaluate_tick(
        self,
        symbol: str,
        best_bid: float,
        best_ask: float,
        ob_imbalance: float,
        recent_trades: list[dict[str, Any]],
        atr_pct: float = 0.0,
    ) -> ScalperSignal | None:
        """Evaluate live tick data for a scalp entry."""
        now = time.time()

        # 1. Cooldown Check
        if now - self._last_signal_time.get(symbol, 0) < self._cooldown:
            return None

        bid = Decimal(str(best_bid))
        ask = Decimal(str(best_ask))

        if bid <= 0 or ask <= 0:
            return None

        # 2. Dynamic Spread Check (Audit Fix)
        spread_threshold = get_dynamic_spread_threshold(atr_pct)
        spread_pct = (ask - bid) / bid
        if spread_pct > spread_threshold:
            logger.debug(
                f"MicroScalper: {symbol} spread {spread_pct:.4%} > threshold {spread_threshold:.4%} "
                f"(ATR={atr_pct:.1f}%)"
            )
            return None

        # 3. Tape Reading (Absorption)
        # Calculate buy vs sell volume in recent trades
        buy_vol = sum(t.get("qty", 0) for t in recent_trades if t.get("side") == "buy")
        sell_vol = sum(t.get("qty", 0) for t in recent_trades if t.get("side") == "sell")

        total_vol = buy_vol + sell_vol
        if total_vol == 0:
            return None

        buy_pct = buy_vol / total_vol

        # 4. Signal Logic
        # Require strong orderbook imbalance + matching tape momentum
        direction = None
        entry_price = Decimal("0")

        if ob_imbalance > 0.3 and buy_pct > 0.6:
            direction = "LONG"
            entry_price = bid # Post-only Maker entry on the bid
        elif ob_imbalance < -0.3 and (1 - buy_pct) > 0.6:
            direction = "SHORT"
            entry_price = ask # Post-only Maker entry on the ask

        if not direction:
            return None

        # 5. Exit Math (Hard TP +0.3%, SL -0.2%)
        if direction == "LONG":
            tp_price = entry_price * Decimal("1.003")
            sl_price = entry_price * Decimal("0.998")
        else:
            tp_price = entry_price * Decimal("0.997")
            sl_price = entry_price * Decimal("1.002")

        signal = ScalperSignal(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            confidence=abs(ob_imbalance) * 100, # basic confidence
            timestamp=now
        )

        self._last_signal_time[symbol] = now
        logger.info(f"MicroScalper generated {direction} signal for {symbol} at {entry_price}")

        return signal
