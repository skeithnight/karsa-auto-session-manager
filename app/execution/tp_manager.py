"""Take-Profit Manager — extracted from ActivePositionManager.

Handles exchange-side TP placement, scale-outs (partial closes),
proactive capital reallocation, and breakeven SL movement.
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.execution.constants import APM_BREAKEVEN_FEE_PCT


class TakeProfitManager:
    """Manages take-profit orders, scale-outs, and breakeven SL movement."""

    def __init__(
        self,
        bybit_client: object,
        position_store: object,
        alert_service: object,
        logger_: Any | None = None,
    ) -> None:
        self._client = bybit_client
        self._store = position_store
        self._alert = alert_service
        self._log = logger_ or logger

    # ------------------------------------------------------------------
    # Exchange-side Take Profit
    # ------------------------------------------------------------------

    async def _ensure_take_profit(
        self,
        pos: dict[str, Any],
        entry_price: Decimal,
        initial_risk: Decimal,
        side: str,
    ) -> None:
        """Place exchange-side TP once for RANGE/CHOP regimes via atomic set_trading_stop."""
        symbol = pos.get("symbol", "")
        api_side = "buy" if side == "LONG" else "sell"
        # TP multiplier: RANGE=1.5:1, CHOP=2.0:1 to cover fees (0.11% round-trip) + slippage
        entry_regime_inner = pos.get("entry_regime", "RANGE")
        if "CHOP" in entry_regime_inner:
            tp_mult = Decimal("2.0")
        else:
            tp_mult = Decimal("1.5")  # RANGE default

        try:
            if side == "LONG":
                tp_price = entry_price + (initial_risk * tp_mult)
            else:
                tp_price = entry_price - (initial_risk * tp_mult)

            await self._client.set_trading_stop(symbol, api_side, take_profit=tp_price)  # type: ignore[attr-defined]
            pos["tp_placed"] = True
            self._log.info(f"APM: atomic TP placed for {symbol} @ {tp_price} ({tp_mult}:1 R/R)")
        except Exception:
            self._log.exception(f"APM: TP placement failed for {symbol}")

    # ------------------------------------------------------------------
    # Scale-out (partial close)
    # ------------------------------------------------------------------

    async def _scale_out_position(self, pos: dict[str, Any], pct: Decimal, entry_price: Decimal, side: str) -> None:
        """Partial close to lock profit. RANGE/CHOP: 50% at +1R. TREND: 30% at +2R."""
        symbol = pos.get("symbol", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            close_qty = (amount * pct).quantize(Decimal("0.001"))
            if close_qty <= 0:
                return
            await self._client.reduce_position(symbol, api_side, close_qty)  # type: ignore[attr-defined]
            pos["scaled_out"] = True
            # Update amount in pos dict so subsequent calculations use reduced quantity
            new_amount = amount - close_qty
            pos["amount"] = str(new_amount)
        except Exception:
            self._log.exception(f"APM: scale-out failed for {symbol}")

    async def proactive_scale_out(
        self, symbol: str, side: str = "LONG", ratio: Decimal = Decimal("0.50")
    ) -> bool:
        """Execute proactive 50% scale-out for dynamic capital reallocation.

        Guarded by idempotency flag `proactive_scale_out_executed`.
        """
        pos = await self._store.get(symbol, side)  # type: ignore[attr-defined]
        if not pos:
            return False

        if pos.get("proactive_scale_out_executed") or str(pos.get("proactive_scale_out_executed")).lower() == "true":
            self._log.info(f"APM proactive_scale_out: {symbol} {side} already scaled out (idempotency guard). Skipping.")
            return False

        entry_price = Decimal(str(pos.get("entry_price", "0")))
        if entry_price <= 0:
            return False

        self._log.warning(
            f"APM CAPITAL REALLOCATION: Proactively scaling out {ratio * 100}% of {symbol} {side} to reallocate capital."
        )
        await self._scale_out_position(pos, ratio, entry_price, side)
        await self._move_stop_to_breakeven(pos, entry_price, side)

        pos["proactive_scale_out_executed"] = True
        try:
            from app.core.position_store import _normalize_side
            side_key = _normalize_side(side)
            redis_key = f"karsa:position:{symbol}:{side_key}"
            await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
        except Exception as e:
            self._log.error(f"APM proactive_scale_out state save failed for {symbol}: {e}")

        if self._alert:
            asyncio.create_task(
                self._alert.send(  # type: ignore[attr-defined]
                    f"CAPITAL REALLOCATION: Proactively scaled out {ratio * 100}% of {symbol} {side}. SL set to Breakeven."
                )
            )
        return True

    # ------------------------------------------------------------------
    # Breakeven
    # ------------------------------------------------------------------

    async def _move_stop_to_breakeven(self, pos: dict[str, Any], entry_price: Decimal, side: str) -> None:
        """Move SL to entry +/- fee buffer. Exchange-side amend with retry."""
        symbol = pos.get("symbol", "")
        sl_order_id = pos.get("sl_order_id", "")
        amount = Decimal(str(pos.get("amount", "0")))
        api_side = "buy" if side == "LONG" else "sell"
        try:
            if side == "LONG":
                # LONG: SL moves to just above entry to cover fees (price must rise to profit)
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT
            else:
                # SHORT: SL must be ABOVE entry (stop out if price rises back through entry)
                # Entry at 100 -> SL at 100.25 (fee buffer above entry)
                new_sl = entry_price + entry_price * APM_BREAKEVEN_FEE_PCT

            new_sl_str = str(new_sl)
            try:
                await self._client.amend_stop_loss(
                    sl_order_id, symbol, api_side, new_sl, amount
                )  # type: ignore[attr-defined]
            except Exception:
                self._log.warning(f"APM: breakeven amend failed for {symbol}, retrying")
                await self._client.amend_stop_loss(
                    sl_order_id, symbol, api_side, new_sl, amount
                )  # type: ignore[attr-defined]

            # BUG-4 fix: persist breakeven flag + new SL price to Redis so this
            # does not re-trigger on every 2s cycle.
            pos["moved_to_breakeven"] = True
            pos["current_sl"] = new_sl_str
            pos["stop_loss"] = new_sl_str
            try:
                from app.core.position_store import _normalize_side

                side_key = _normalize_side(side)
                redis_key = f"karsa:position:{pos.get('symbol', '')}:{side_key}"
                await self._store.redis.set(redis_key, _json.dumps(pos))  # type: ignore[attr-defined]
            except Exception:
                self._log.exception(f"APM: failed to persist breakeven flag for {symbol}")

            await self._store.update_sl(symbol, api_side, sl_order_id, new_sl)  # type: ignore[attr-defined]
            self._log.info(f"APM: breakeven locked for {symbol} at {new_sl_str}")

        except Exception:
            self._log.exception(f"APM: breakeven CRITICAL failure for {symbol}")
            if self._alert:
                await self._alert.send(f"APM breakeven FAILED for {symbol}")  # type: ignore[attr-defined]
