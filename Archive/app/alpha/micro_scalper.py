"""Micro Scalper Engine — 1m/tick Quick Win Strategy.

- Entry: Orderbook Delta, Tape Reading
- Execution: STRICTLY is_post_only=True
- Aborts if spread > 0.04%
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from loguru import logger
from pydantic import BaseModel


class ScalperSignal(BaseModel):
    symbol: str
    direction: str
    entry_price: Decimal
    sl_price: Decimal
    tp_price: Decimal
    confidence: float
    timestamp: float


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
        recent_trades: list[dict[str, Any]]
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

        # 2. Spread Check (< 0.04%)
        spread_pct = (ask - bid) / bid
        if spread_pct > Decimal("0.0004"):
            # Spread too wide for scalping
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
