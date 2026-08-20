"""ShadowExecutor — virtual order execution for shadow mode.

Drop-in replacement for SmartOrderRouter that records virtual entries/exits
with fees/slippage without placing real orders.

Refinements applied:
  1. Fee asymmetry: maker (0.02%) vs taker (0.055%) based on is_post_only
  2. Dynamic spread-based slippage penalty
  3. Pending limit orders: PENDING_VIRTUAL_FILL state for post-only entries
"""

from __future__ import annotations

import json as _json
import time
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any

from loguru import logger

from app.core import metrics
from app.core.config import get_settings
from app.core.redis_client import RedisClient


class ShadowExecutor:
    """Simulated order routing. Same execute/execute_exit interface as SOR.

    Refinement 1: is_post_only param routes to maker vs taker fee.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        position_store: object,
        trade_store: object,
        alert_service: object | None = None,
    ) -> None:
        self._redis = redis_client
        self._pos_store = position_store
        self._trade_store = trade_store
        self._alert = alert_service
        self._counter = 0
        settings = get_settings()
        self._slippage = Decimal(settings.shadow_slippage_pct)
        self._taker_fee = Decimal(settings.shadow_taker_fee_pct)
        self._maker_fee = Decimal(settings.shadow_maker_fee_pct)

    def _next_id(self) -> str:
        self._counter += 1
        return f"SHADOW-{uuid.uuid4().hex[:8]}"

    def _pick_fee(self, is_post_only: bool) -> Decimal:
        """Fee asymmetry: maker (post-only) vs taker (market/IOC)."""
        return self._maker_fee if is_post_only else self._taker_fee

    async def _get_mid_price(
        self, symbol: str, fallback_price: Decimal | None = None
    ) -> Decimal:
        """Read live mid price. Checks cached shadow price first, then system keys."""
        # Check shadow-cached price (written on execute)
        cached = await self._redis.get(f"shadow:price:{symbol}")
        if cached:
            try:
                price = Decimal(cached)
                if price > 0:
                    return price
            except Exception:
                pass
        # Check global:state keys (written by RedisClient.set_global_state)
        raw = await self._redis.get(f"global:state:{symbol}")
        if raw:
            try:
                data = _json.loads(raw)
                bid = Decimal(str(data.get("bid", "0")))
                ask = Decimal(str(data.get("ask", "0")))
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2
                last = Decimal(str(data.get("last", "0")))
                if last > 0:
                    return last
            except Exception:
                pass
        raw2 = await self._redis.get(f"ticker:{symbol}")
        if raw2:
            try:
                data = _json.loads(raw2)
                last = Decimal(str(data.get("last", "0")))
                if last > 0:
                    return last
            except Exception:
                pass
        if fallback_price is not None and fallback_price > 0:
            return fallback_price
        raise ValueError(f"ShadowExecutor: no price for {symbol}")

    def _apply_slippage(self, price: Decimal, side: str) -> Decimal:
        """Apply fallback slippage — worse fill for the trader."""
        if side in ("buy", "LONG"):
            return (price * (1 + self._slippage)).quantize(
                Decimal("0.00000001"), rounding=ROUND_DOWN
            )
        return (price * (1 - self._slippage)).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )

    async def _compute_dynamic_slippage(self, symbol: str, price: Decimal, side: str) -> Decimal:
        """Dynamic Spread-Based Slippage Penalty:
        spread_bps = ((best_ask - best_bid) / mid) * 10000
        dynamic_slippage_bps = max(2.0, min(15.0, spread_bps / 2.0))
        """
        try:
            raw = await self._redis.get(f"global:state:{symbol}")
            if raw:
                data = _json.loads(raw)
                bid = Decimal(str(data.get("bid", "0")))
                ask = Decimal(str(data.get("ask", "0")))
                if bid > 0 and ask > bid:
                    mid = (bid + ask) / Decimal("2")
                    spread_bps = ((ask - bid) / mid) * Decimal("10000")
                    dynamic_bps = max(Decimal("2.0"), min(Decimal("15.0"), spread_bps / Decimal("2.0")))
                    slip_mult = (dynamic_bps / Decimal("10000"))
                    if side in ("buy", "LONG"):
                        return (price * (Decimal("1") + slip_mult)).quantize(Decimal("0.00000001"))
                    else:
                        return (price * (Decimal("1") - slip_mult)).quantize(Decimal("0.00000001"))
        except Exception:
            pass
        return self._apply_slippage(price, side)

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal | None = None,
        price_tick: Decimal | None = None,
        max_loss_usd: Decimal | None = None,
        is_post_only: bool = False,
    ) -> dict | None:
        """Virtual entry. Returns fake order dict matching SOR output.

        Refinement 4: When is_post_only=True, returns status=PENDING
        so ShadowAPM can simulate limit fill timing.
        """
        if amount <= 0:
            return None

        mid = await self._get_mid_price(symbol, fallback_price=price)
        fill_price = await self._compute_dynamic_slippage(symbol, mid, side)

        # Cache mid price for APM monitoring (SL/TP checks)
        await self._redis.set(f"shadow:price:{symbol}", str(mid), ex=300)

        fee_rate = self._pick_fee(is_post_only)
        fee = (fill_price * amount * fee_rate).quantize(Decimal("0.01"))

        order_id = self._next_id()
        sl_id = f"SHADOW-SL-{uuid.uuid4().hex[:8]}"

        # Refinement 4: pending state for limit orders
        status = "PENDING_VIRTUAL_FILL" if is_post_only else "filled"

        logger.info(
            f"SHADOW ENTRY: {symbol} {side} {amount} @ {fill_price} "
            f"(mid={mid}, fee={fee}, fee_type={'maker' if is_post_only else 'taker'}, "
            f"status={status})"
        )

        metrics.karsa_shadow_orders_placed_total.labels(symbol=symbol, side=side).inc()

        result: dict[str, Any] = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "price": fill_price,
            "amount": amount,
            "status": status,
            "sl_order_id": sl_id,
            "fee": str(fee),
            "fee_type": "maker" if is_post_only else "taker",
            "is_shadow": True,
        }
        if is_post_only:
            result["pending_since"] = time.time()

        return result

    async def execute_exit(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal | None = None,
        reason: str = "manual",
    ) -> dict | None:
        """Virtual exit. Returns fake fill dict. Exits always taker."""
        if amount <= 0:
            return None

        mid = await self._get_mid_price(symbol)
        exit_side = "sell" if side == "LONG" else "buy"
        fill_price = self._apply_slippage(mid, exit_side)

        fee = (fill_price * amount * self._taker_fee).quantize(Decimal("0.01"))

        logger.info(
            f"SHADOW EXIT: {symbol} {exit_side} {amount} @ {fill_price} "
            f"(reason={reason}, fee={fee})"
        )

        metrics.karsa_shadow_exits_placed_total.labels(
            symbol=symbol, reason=reason
        ).inc()

        return {
            "id": self._next_id(),
            "symbol": symbol,
            "side": exit_side,
            "price": fill_price,
            "amount": amount,
            "status": "filled",
            "fee": str(fee),
            "reason": reason,
            "is_shadow": True,
        }

    async def cancel_all(self, symbol: str) -> None:
        """No-op — no real orders to cancel."""
        pass

    async def cancel_all_positions(self) -> None:
        """No-op — no real orders to cancel."""
        pass
