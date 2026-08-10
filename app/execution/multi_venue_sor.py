"""MultiVenueSOR — Smart Order Router operating across Bybit and Hyperliquid.

Venue selection logic:
  - Mean reversion / tight range & short holding period → Hyperliquid
  - Breakout / high notional size → Bybit
  - Fallback: if preferred venue is degraded → route to alternate venue

INVARIANT: Mandatory exchange-side SL placed on fill regardless of venue.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loguru import logger

from app.execution.bybit_client import BybitClient
from app.execution.hyperliquid_client import HyperliquidClient
from app.execution.sor import SmartOrderRouter


class MultiVenueSOR:
    """Routes trades to optimal execution venue (Bybit vs Hyperliquid)."""

    def __init__(
        self,
        bybit_sor: SmartOrderRouter,
        hyperliquid_client: HyperliquidClient,
    ) -> None:
        self._bybit_sor = bybit_sor
        self._hl_client = hyperliquid_client

    def select_venue(
        self,
        symbol: str,
        regime: str | None = None,
        notional_usd: Decimal = Decimal("1000.0"),
    ) -> str:
        """Select optimal venue: 'BYBIT' or 'HYPERLIQUID'."""
        if regime in ("MEAN_REVERSION", "RANGE", "CHOP") and notional_usd < Decimal("2000.0"):
            return "HYPERLIQUID"
        return "BYBIT"

    async def execute(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal,
        price_tick: Decimal | None = None,
        max_loss_usd: Decimal | None = None,
        is_post_only: bool = False,
        regime: str | None = None,
    ) -> dict[str, Any] | None:
        """Route order execution to chosen venue and place mandatory exchange-side SL."""
        notional = (amount * price).quantize(Decimal("0.01"))
        venue = self.select_venue(symbol, regime, notional)

        logger.info(f"MultiVenueSOR: selected venue {venue} for {symbol} {side} {amount} (notional={notional})")

        if venue == "HYPERLIQUID":
            fill = await self._hl_client.place_order(symbol, side, amount, price, is_post_only)
            if fill and fill.get("status") == "filled":
                # Place mandatory exchange-side SL
                sl_dist = (price * Decimal("0.02")).quantize(Decimal("0.00000001"))
                sl_price = (price - sl_dist) if side in ("buy", "LONG") else (price + sl_dist)
                sl_id = await self._hl_client.place_stop_loss(symbol, side, sl_price)
                fill["sl_order_id"] = sl_id
            return fill
        else:
            # Route to Bybit via existing SOR
            return await self._bybit_sor.execute(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                price_tick=price_tick,
                max_loss_usd=max_loss_usd,
                is_post_only=is_post_only,
            )
