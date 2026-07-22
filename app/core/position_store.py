"""Position Store — Redis-backed lifecycle tracking per position.

Tracks: entry price, peak price, ATR, SL order ID, checkpoint state, timestamps.
Key: karsa:position:{symbol}:{side}   (canonical side: LONG or SHORT)
Callers: main.py, TrailingStopManager, CheckpointManager, SectorCap, executor_task.

Side normalization: Bybit returns "buy"/"sell"; signals use "LONG"/"SHORT".
All callers MUST go through _normalize_side() so the Redis key is always
"LONG" or "SHORT" — never "buy"/"sell" or "Buy"/"Sell".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.core.redis_client import RedisClient


def _normalize_side(side: str) -> str:
    """Translate any side variant to canonical 'LONG' or 'SHORT'.

    Accepted inputs: 'buy', 'Buy', 'LONG', 'sell', 'Sell', 'SHORT'.
    Falls back to 'LONG' for unrecognised values so callers never crash.
    """
    if side in ("buy", "Buy", "LONG"):
        return "LONG"
    if side in ("sell", "Sell", "SHORT"):
        return "SHORT"
    logger.warning("position_store: unknown side=%r — defaulting to LONG", side)
    return "LONG"


class PositionStore:
    """Redis-backed position lifecycle state.

    ponytail: flat dict per position, no ORM. Simple get/set/del.
    """

    def __init__(self, redis_client: RedisClient) -> None:
        logger.debug("PositionStore.__init__: entering")
        self.redis = redis_client
        logger.debug("PositionStore.__init__: returning")

    def _key(self, symbol: str, side: str) -> str:
        """Build Redis key. Side is always normalised to LONG/SHORT."""
        return f"karsa:position:{symbol}:{_normalize_side(side)}"

    async def save(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        amount: Decimal,
        sl_order_id: str | None = None,
        atr: Decimal | None = None,
        entry_confidence: float | None = None,
        regime: str | None = None,
        entry_regime: str | None = None,
        initial_risk_per_unit: str | None = None,
        risk_profile_json: str | None = None,
        virtual_sl: str | None = None,
        virtual_tp: str | None = None,
    ) -> None:
        """Save new position state."""
        key = self._key(symbol, side)
        canonical_side = _normalize_side(side)
        data = {
            "symbol": symbol,
            "side": canonical_side,  # always LONG/SHORT inside Redis
            "entry_price": str(entry_price),
            "amount": str(amount),
            "peak_price": str(entry_price),
            "sl_order_id": sl_order_id or "",
            # BUG-2 fix: guard must not erase Decimal("0") as falsy
            "atr": str(atr) if (atr is not None and atr > Decimal("0")) else "",
            "entry_confidence": str(entry_confidence)
            if entry_confidence is not None
            else "",
            "regime": regime or "",
            "checkpoint": "OPEN",
            "entry_time": datetime.now(UTC).isoformat(),
            "entered_at": datetime.now(UTC).isoformat(),  # Keep for backwards compatibility
            "last_check_at": datetime.now(UTC).isoformat(),
            "entry_regime": entry_regime or regime or "",
            "initial_risk_per_unit": initial_risk_per_unit or "",
            "risk_profile_json": risk_profile_json or "",
            # Explicit boolean flags so APM can read them without guessing
            "moved_to_breakeven": False,
            "tp_placed": False,
            "scaled_out": False,
        }
        if virtual_sl:
            data["virtual_sl"] = virtual_sl
        if virtual_tp:
            data["virtual_tp"] = virtual_tp
        await self.redis.set(key, json.dumps(data))
        logger.info(f"Position saved: {symbol} {side} @ {entry_price}")

    async def get(self, symbol: str, side: str) -> dict[str, Any] | None:
        """Get position state."""
        key = self._key(symbol, side)
        raw = await self.redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def update_peak(self, symbol: str, side: str, price: Decimal) -> None:
        """Update peak price (HWM for LONG, LWM for SHORT)."""
        pos = await self.get(symbol, side)
        if not pos:
            return

        norm_side = _normalize_side(side)
        current_peak = pos.get("peak_price")

        if current_peak is None:
            updated = True
        else:
            peak = Decimal(current_peak)
            if norm_side == "LONG":
                updated = price > peak
            else:
                updated = price < peak

        if updated:
            pos["peak_price"] = str(price)
            pos["last_check_at"] = datetime.now(UTC).isoformat()
            await self.redis.set(self._key(symbol, side), json.dumps(pos))

    async def update_sl(
        self,
        symbol: str,
        side: str,
        sl_order_id: str,
        new_sl_price: Decimal | None = None,
    ) -> None:
        """Update SL order ID and optionally persist the new SL price.

        BUG-7 fix: callers that amend the SL price must also persist it here so
        the $1 cap check and reconcile loop read the correct current_sl value.
        """
        pos = await self.get(symbol, side)
        if not pos:
            return
        pos["sl_order_id"] = sl_order_id
        if new_sl_price is not None and new_sl_price > Decimal("0"):
            pos["current_sl"] = str(new_sl_price)
            pos["stop_loss"] = str(new_sl_price)
        pos["last_check_at"] = datetime.now(UTC).isoformat()
        await self.redis.set(self._key(symbol, side), json.dumps(pos))

    async def update_checkpoint(self, symbol: str, side: str, checkpoint: str) -> None:
        """Update checkpoint state."""
        pos = await self.get(symbol, side)
        if not pos:
            return
        pos["checkpoint"] = checkpoint
        pos["last_check_at"] = datetime.now(UTC).isoformat()
        await self.redis.set(self._key(symbol, side), json.dumps(pos))

    async def remove(self, symbol: str, side: str) -> None:
        """Remove position (on close). Side is normalised automatically."""
        key = self._key(symbol, side)
        await self.redis.delete(key)
        logger.info(f"Position removed: {symbol} {_normalize_side(side)}")

    async def has_position(self, symbol: str, side: str | None = None) -> bool:
        """Check if position exists.

        BUG-9 fix: normalise side so 'buy'/'sell' from Bybit and 'LONG'/'SHORT'
        from signals both resolve to the correct Redis key.
        """
        if side:
            return await self.get(symbol, _normalize_side(side)) is not None
        # Check both directions (canonical keys only)
        long_pos = await self.get(symbol, "LONG")
        short_pos = await self.get(symbol, "SHORT")
        return long_pos is not None or short_pos is not None

    async def list_all(self) -> list[dict[str, Any]]:
        """List all active positions."""
        keys = await self.redis.keys("karsa:position:*")
        positions = []
        for key in keys:
            raw = await self.redis.get(key)
            if raw:
                try:
                    positions.append(json.loads(raw))
                except Exception:
                    pass
        return positions

    async def cleanup_stale(self, exchange_symbols: set[str]) -> int:
        """Remove position keys for symbols no longer held on exchange.

        Args:
            exchange_symbols: set of Bybit-format symbols (e.g. "BTCUSDT") from fetch_positions().

        Returns:
            Number of orphaned keys removed.
        """
        keys = await self.redis.keys("karsa:position:*")
        removed = 0
        for key in keys:
            key_str = key if isinstance(key, str) else key.decode()
            raw = await self.redis.get(key)
            if not raw:
                await self.redis.delete(key_str)
                removed += 1
                continue
            try:
                pos = json.loads(raw)
                sym = pos.get("symbol", "")
                # Convert ccxt format (BTC/USDT) to Bybit format (BTCUSDT)
                bybit_sym = sym.replace("/", "")
                if bybit_sym not in exchange_symbols:
                    await self.redis.delete(key_str)
                    logger.info(
                        f"Cleaned orphaned position: {sym} {pos.get('side', '')}"
                    )
                    removed += 1
            except Exception:
                await self.redis.delete(key_str)
                removed += 1
        return removed

    async def update_fields(
        self, symbol: str, side: str, updates: dict[str, Any]
    ) -> None:
        """Atomically patch arbitrary fields on a position.

        Used by the position health-check scheduler to fill missing fields
        without rewriting the entire record (avoids overwriting live flags).
        """
        pos = await self.get(symbol, side)
        if not pos:
            logger.warning("update_fields: no position found for %s %s", symbol, side)
            return
        pos.update(updates)
        pos["last_check_at"] = datetime.now(UTC).isoformat()
        await self.redis.set(self._key(symbol, side), json.dumps(pos))
        logger.debug(
            "update_fields: patched %s %s fields=%s", symbol, side, list(updates.keys())
        )

    async def get_missing_fields(
        self, symbol: str, side: str, required: list[str]
    ) -> list[str]:
        """Return list of required field names that are empty/missing on this position.

        Used by position health-check scheduler.
        """
        pos = await self.get(symbol, side)
        if not pos:
            return required  # entire position is missing
        missing = []
        for f in required:
            val = pos.get(f, "")
            if val is None or str(val).strip() in ("", "0", "None"):
                missing.append(f)
        return missing
