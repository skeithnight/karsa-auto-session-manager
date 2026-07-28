"""ELO Refresh Loop.

Refreshes ELO ratings for strategies every 5 minutes.
Reads recent trades and computes ELO updates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def elo_refresh_loop(
    trade_store: Any,
    redis_client: Any,
    shutdown_event: asyncio.Event,
    interval_s: int = 300,
) -> None:
    """Refresh ELO ratings every 5 minutes.

    Reads recent trades and computes ELO updates for each strategy.
    Writes to Redis: karsa:elo:{regime}:{direction}

    Args:
        trade_store: TradeStore instance for reading trades.
        redis_client: Redis client for writing results.
        shutdown_event: Event to signal shutdown.
        interval_s: Interval in seconds (default: 300).
    """
    while not shutdown_event.is_set():
        try:
            trades = await trade_store.get_recent_trades(count=50)
            if not trades:
                await asyncio.sleep(interval_s)
                continue

            # Group trades by strategy (regime:direction)
            strategy_trades: dict[str, list] = {}
            for trade in trades:
                regime = trade.get("regime", "RANGE")
                direction = trade.get("side", "LONG")
                key = f"{regime}:{direction}"
                if key not in strategy_trades:
                    strategy_trades[key] = []
                strategy_trades[key].append(trade)

            # Compute ELO for each strategy
            import json as _json
            for strategy_key, strades in strategy_trades.items():
                try:
                    # Read current ELO
                    raw = await redis_client.get(f"karsa:elo:{strategy_key}")
                    if raw:
                        data = _json.loads(raw)
                        current_elo = data.get("elo", 1500.0)
                        wins = data.get("wins", 0)
                        losses = data.get("losses", 0)
                    else:
                        current_elo = 1500.0
                        wins = 0
                        losses = 0

                    # Calculate win rate from recent trades
                    for t in strades:
                        pnl = t.get("realized_pnl", 0)
                        if pnl > 0:
                            wins += 1
                        elif pnl < 0:
                            losses += 1

                    # Simple ELO update
                    win_rate = wins / max(1, wins + losses)
                    expected = 1.0 / (1.0 + 10 ** ((1500 - current_elo) / 400))
                    k_factor = 32.0
                    new_elo = current_elo + k_factor * (win_rate - expected)

                    # Write updated ELO
                    await redis_client.set(
                        f"karsa:elo:{strategy_key}",
                        _json.dumps({
                            "elo": new_elo,
                            "wins": wins,
                            "losses": losses,
                            "win_rate": win_rate,
                        })
                    )
                except Exception as e:
                    logger.debug("elo_refresh: failed for %s: %s", strategy_key, e)

        except Exception as e:
            logger.error("elo_refresh_loop failed: %s", e)

        await asyncio.sleep(interval_s)
