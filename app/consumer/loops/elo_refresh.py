"""ELO Refresh Loop.

Refreshes ELO ratings for strategies every 5 minutes.
Reads recent trades and computes ELO updates.

Writes to Redis: karsa:elo:{regime}:{direction}
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
    K_FACTOR = 32.0
    BASELINE_ELO = 1500.0
    processed_key = "karsa:elo:last_processed_time"

    while not shutdown_event.is_set():
        try:
            # Get recent trades (TradeStore uses `limit` parameter)
            trades = await trade_store.get_recent_trades(limit=50)
            if not trades:
                await asyncio.sleep(interval_s)
                continue

            # Get last processed trade time to avoid reprocessing
            last_time = None
            try:
                raw = await redis_client.get(processed_key)
                if raw:
                    last_time = raw.decode() if isinstance(raw, bytes) else str(raw)
            except Exception:
                pass

            import json as _json

            for trade in trades:
                # Use exit_time as unique key
                trade_time = trade.get("exit_time", "")
                if not trade_time:
                    continue
                if last_time and trade_time <= last_time:
                    continue

                regime = trade.get("regime", "UNKNOWN")
                direction = trade.get("side", "LONG")
                pnl_pct = float(trade.get("realized_pnl", 0))
                strategy_key = f"{regime}:{direction}"

                # Read current ELO
                elo_raw = await redis_client.get(f"karsa:elo:{strategy_key}")
                current_elo = BASELINE_ELO
                wins = 0
                losses = 0
                if elo_raw:
                    try:
                        elo_data = _json.loads(elo_raw)
                        current_elo = elo_data.get("elo", BASELINE_ELO)
                        wins = elo_data.get("wins", 0)
                        losses = elo_data.get("losses", 0)
                    except Exception:
                        pass

                # Update ELO: win = 1.0, loss = 0.0
                score = 1.0 if pnl_pct > 0 else 0.0
                new_elo = current_elo + K_FACTOR * (score - 0.5)
                if pnl_pct > 0:
                    wins += 1
                else:
                    losses += 1

                await redis_client.set(f"karsa:elo:{strategy_key}", _json.dumps({
                    "elo": round(new_elo, 2),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(wins / max(1, wins + losses), 4),
                    "last_trade_time": trade_time,
                }))

                if abs(new_elo - current_elo) > 5:
                    logger.info(
                        "elo_refresh: %s %.0f → %.0f (trade=%s, pnl=%.2f%%)",
                        strategy_key, current_elo, new_elo, trade_time, pnl_pct,
                    )

                last_time = trade_time

            # Persist last processed time
            if last_time:
                await redis_client.set(processed_key, last_time)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("elo_refresh_loop failed: %s", e)

        await asyncio.sleep(interval_s)
