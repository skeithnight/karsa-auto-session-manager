"""Batch upsert handler for historical candles.

Persists normalized OHLCV data to the ``historical_candles`` table.
Idempotent via ON CONFLICT DO NOTHING. Converts float prices to
Decimal for storage, matching the project's money-is-Decimal rule.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

# ── Table DDL (for reference — run via migration script separately) ─────
# CREATE TABLE IF NOT EXISTS historical_candles (
#     id          SERIAL PRIMARY KEY,
#     symbol      VARCHAR(30) NOT NULL,
#     timeframe   VARCHAR(10) NOT NULL DEFAULT '1h',
#     ts          TIMESTAMPTZ NOT NULL,
#     open        NUMERIC(20,8) NOT NULL,
#     high        NUMERIC(20,8) NOT NULL,
#     low         NUMERIC(20,8) NOT NULL,
#     close       NUMERIC(20,8) NOT NULL,
#     volume      NUMERIC(20,8) NOT NULL,
#     created_at  TIMESTAMPTZ DEFAULT NOW(),
#     UNIQUE(symbol, timeframe, ts)
# );
# CREATE INDEX IF NOT EXISTS idx_candles_lookup
#     ON historical_candles (symbol, timeframe, ts);


def _candle_to_row(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    candle: list,
) -> tuple:
    """Convert raw ccxt candle to a row tuple for batch insert.

    Args:
        exchange_id: Exchange identifier (not stored, used for prefixed symbol).
        symbol: Unified symbol (e.g. BTC/USDT).
        timeframe: Timeframe string (e.g. 1h).
        candle: Raw ccxt OHLCV [ts_ms, open, high, low, close, volume].

    Returns:
        Tuple (symbol, timeframe, ts, open, high, low, close, volume)
        with prices as Decimals.
    """
    ts_ms, open_p, high_p, low_p, close_p, volume = candle
    ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)

    return (
        symbol,
        timeframe,
        ts,
        Decimal(str(open_p)),
        Decimal(str(high_p)),
        Decimal(str(low_p)),
        Decimal(str(close_p)),
        Decimal(str(volume)),
    )


_INSERT_SQL = """
    INSERT INTO historical_candles (symbol, timeframe, ts, open, high, low, close, volume)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (symbol, timeframe, ts) DO NOTHING
"""


async def bulk_upsert(
    conn: Any,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    candles: list[list],
    batch_size: int = _BATCH_SIZE,
) -> int:
    """Insert candles into historical_candles in batches, skipping duplicates.

    Args:
        conn: An asyncpg connection or pool ``acquire()`` proxy.
        exchange_id: Exchange identifier (used for logging only).
        symbol: Unified symbol.
        timeframe: Candle timeframe.
        candles: List of raw ccxt OHLCV lists.
        batch_size: Max rows per batch.

    Returns:
        Total number of rows inserted (sum across batches).
    """
    if not candles:
        logger.debug("bulk_upsert called with empty candle list — skipping")
        return 0

    total = 0
    for i in range(0, len(candles), batch_size):
        batch = candles[i : i + batch_size]
        rows = [_candle_to_row(exchange_id, symbol, timeframe, c) for c in batch]

        result = await conn.executemany(_INSERT_SQL, rows)
        # executemany returns 'INSERT 0 N' string — parse count
        if isinstance(result, str) and result.startswith("INSERT"):
            count = int(result.split()[-1])
            total += count
        else:
            # asyncpg may return None or a different format
            total += len(batch)

    logger.info(
        "upserted %d / %d candles for %s %s %s",
        total,
        len(candles),
        symbol,
        timeframe,
        exchange_id,
    )
    return total
