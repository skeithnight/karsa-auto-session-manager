"""Bootstrap local deployment — ingest historical data if DB is empty.

Checks if historical_candles table is empty on first boot.
If empty, triggers historical ingestion for the default universe
(Tier 1 symbols: BTC/USDT, ETH/USDT, SOL/USDT) using Bybit REST API.

Usage:
    python scripts/bootstrap_local.py
    python scripts/bootstrap_local.py --symbols BTC/USDT,ETH/USDT --days 30
    python scripts/bootstrap_local.py --check-only  # just check if bootstrapped
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import ccxt.async_support as ccxt
from loguru import logger

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_DAYS = 90
DEFAULT_TIMEFRAME = "1h"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap local deployment with historical data")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help=f"Comma-separated symbols (default: {','.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"Lookback days (default: {DEFAULT_DAYS})")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help=f"Candle timeframe (default: {DEFAULT_TIMEFRAME})")
    parser.add_argument("--check-only", action="store_true", help="Only check if already bootstrapped")
    parser.add_argument("--force", action="store_true", help="Force re-bootstrap even if data exists")
    parser.add_argument("--db-url", default="", help="PostgreSQL URL (default: from env)")
    return parser.parse_args()


async def check_already_bootstrapped(pool: asyncpg.Pool) -> bool:
    """Check if historical_candles table has data."""
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM historical_candles")
        return count is not None and count > 0


async def _candle_to_row(symbol: str, timeframe: str, candle: list) -> tuple:
    """Convert raw ccxt candle to row tuple."""
    ts_ms, open_p, high_p, low_p, close_p, volume = candle
    ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
    return (symbol, timeframe, ts, Decimal(str(open_p)), Decimal(str(high_p)),
            Decimal(str(low_p)), Decimal(str(close_p)), Decimal(str(volume)))


_INSERT_SQL = """
    INSERT INTO historical_candles (symbol, timeframe, ts, open, high, low, close, volume)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (symbol, timeframe, ts) DO NOTHING
"""


async def ingest_symbol(
    exchange: ccxt.Exchange,
    pool: asyncpg.Pool,
    symbol: str,
    timeframe: str,
    days: int,
) -> int:
    """Fetch and ingest historical candles for one symbol."""
    timeframe_ms = _timeframe_to_ms(timeframe)
    now_ms = int(time.time() * 1000)
    target_ms = now_ms - (days * 86_400_000)

    all_candles: dict[int, list] = {}
    since = target_ms

    while since < now_ms:
        try:
            batch = await exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as exc:
            logger.warning("fetch_ohlcv failed for %s: %s", symbol, exc)
            break
        if not batch:
            break
        for c in batch:
            all_candles[c[0]] = c
        since = batch[-1][0] + timeframe_ms

    candles = sorted(all_candles.values(), key=lambda c: c[0])

    if not candles:
        logger.info("  %s: no candles fetched", symbol)
        return 0

    rows = []
    for c in candles:
        rows.append(await _candle_to_row(symbol, timeframe, c))

    async with pool.acquire() as conn:
        await conn.executemany(_INSERT_SQL, rows)

    logger.info("  %s: ingested %d candles", symbol, len(rows))
    return len(rows)


def _timeframe_to_ms(tf: str) -> int:
    """Convert timeframe string to milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    num = int(tf[:-1])
    return num * units.get(tf[-1], 3_600_000)


async def main() -> None:
    args = parse_args()
    symbols = [s.strip() for s in args.symbols.split(",")]

    db_url = args.db_url or "postgresql://karsa:karsa@localhost:5432/karsa"
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)

    if args.check_only:
        bootstrapped = await check_already_bootstrapped(pool)
        print("BOOTSTRAPPED" if bootstrapped else "NOT_BOOTSTRAPPED")
        await pool.close()
        return

    if not args.force:
        bootstrapped = await check_already_bootstrapped(pool)
        if bootstrapped:
            logger.info("Database already has historical data — skipping bootstrap")
            await pool.close()
            return

    logger.info("Bootstrapping %d symbols, %d days, %s candles", len(symbols), args.days, args.timeframe)

    exchange = ccxt.bybit({"enableRateLimit": True})

    total = 0
    for sym in symbols:
        logger.info("Ingesting %s...", sym)
        count = await ingest_symbol(exchange, pool, sym, args.timeframe, args.days)
        total += count

    await exchange.close()
    await pool.close()

    logger.info("Bootstrap complete: %d candles ingested", total)
    print(f"BOOTSTRAPPED: {total} candles across {len(symbols)} symbols")


if __name__ == "__main__":
    asyncio.run(main())
