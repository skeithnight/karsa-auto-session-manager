"""Wallet Metrics Loop.

Periodically publishes wallet balance, position metrics, and max positions to Prometheus.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


async def wallet_metrics_loop(
    bybit: Any,
    position_store: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 30,
) -> None:
    """Periodically publish wallet balance, position metrics, and max positions to Prometheus.

    Args:
        bybit: BybitClient instance.
        position_store: PositionStore instance.
        redis_client: Redis client.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 30).
    """
    from app.core import metrics

    while not shutdown_event.is_set():
        try:
            # Get max positions
            try:
                max_pos = int(await redis_client.get("karsa:settings:max_positions") or 5)
            except Exception:
                max_pos = 5
            metrics.max_positions.set(max_pos)

            # Get wallet balance
            if bybit:
                wallet = await bybit.get_wallet_balance()
                available = float(wallet.get("available", 0))
                balance = float(wallet.get("balance", 0))
                metrics.wallet_balance.set(available)

            # Get open positions
            open_positions = await position_store.list_all()

            # Wallet equity = balance + unrealized PnL
            if bybit:
                total_unrealized = sum(float(p.get("pnl", 0)) for p in open_positions)
                metrics.wallet_total_equity.set(balance + total_unrealized)

            # Per-position metrics
            for pos in open_positions:
                sym = pos.get("symbol", "")
                if not sym:
                    continue
                side = pos.get("side", "LONG")
                entry = Decimal(str(pos.get("entry_price", 0)))
                amount = Decimal(str(pos.get("amount", 0)))
                pnl = Decimal(str(pos.get("pnl", 0)))

                metrics.position_entry_price.labels(symbol=sym, side=side).set(float(entry))
                metrics.position_amount.labels(symbol=sym, side=side).set(float(amount))
                metrics.position_pnl.labels(symbol=sym, side=side).set(float(pnl))

            # Open position count
            metrics.open_positions.set(len(open_positions))

            logger.debug("wallet_metrics: updated (positions=%d)", len(open_positions))

        except Exception as e:
            logger.error("wallet_metrics_loop failed: %s", e)

        await asyncio.sleep(interval_s)
