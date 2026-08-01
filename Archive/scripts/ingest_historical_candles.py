"""Ingest historical OHLCV candles from exchanges into PostgreSQL.

Usage:
    python -m scripts.ingest_historical_candles --symbols BTCUSDT,ETHUSDT --timeframe 1h --days 90
    python -m scripts.ingest_historical_candles --symbols ALL --timeframe 1h --days 30 --exchange binance

Paginates backwards using `since` parameter to handle exchange limits.
Idempotent: ON CONFLICT DO NOTHING for safe re-runs.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import ccxt.async_support as ccxt
from loguru import logger

MAX_PER_REQUEST = 1000
RETRY_DELAY_S = 5
RATE_LIMIT_BACKOFF_S = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest historical OHLCV candles")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols (e.g. BTCUSDT,ETHUSDT) or ALL",
    )
    parser.add_argument(
        "--timeframe", default="1h", help="Candle timeframe (default: 1h)"
    )
    parser.add_argument("--days", type=int, default=90, help="Lookback days (default: 90)")
    parser.add_argument(
        "--exchange",
        default="bybit",
        choices=["bybit", "binance", "okx"],
        help="Exchange (default: bybit)",
    )
    parser.add_argument(
        "--db-url",
        default="",
        help="PostgreSQL URL (default: from app.core.config)",
    )
    return parser.parse_args()


# ── Exchange helpers ──────────────────────────────────────


def _make_exchange(exchange_id: str) -> ccxt.Exchange:
    """Create a ccxt async exchange instance for REST fetching."""
    exchange_class = getattr(ccxt, exchange_id)
    opts: dict = {"enableRateLimit": True}
    if exchange_id == "bybit":
        opts["options"] = {"defaultType": "swap"}
    return exchange_class(opts)


def _standardize_symbol(symbol: str) -> str:
    """Convert user input to unified symbol format.

    BTCUSDT → BTC/USDT, ETHUSDT → ETH/USDT
    """
    s = symbol.upper().strip()
    if "/" in s:
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s


def _exchange_market_id(symbol: str, exchange: ccxt.Exchange) -> str:
    """Resolve unified symbol to exchange-specific market ID."""
    try:
        return exchange.market(symbol)["id"]
    except Exception:
        swap = f"{symbol}:USDT"
        try:
            return exchange.market(swap)["id"]
        except Exception:
            return symbol


# ── Paginated fetch ────────────────────────────────────────


async def fetch_candles_range(
    exchange: ccxt.Exchange,
    market_id: str,
    timeframe: str,
    since_ms: int,
    limit: int = MAX_PER_REQUEST,
) -> list[list]:
    """Fetch candles with retry on rate limit."""
    for attempt in range(3):
        try:
            return await exchange.fetch_ohlcv(market_id, timeframe, since=since_ms, limit=limit) or []
        except Exception as e:
            err = str(e).lower()
            if "rate limit" in err or "429" in err:
                logger.warning("Rate limited — backing off {}s", RATE_LIMIT_BACKOFF_S)
                await asyncio.sleep(RATE_LIMIT_BACKOFF_S)
                continue
            logger.warning("Fetch error (attempt {}/3): {}", attempt + 1, e)
            await asyncio.sleep(RETRY_DELAY_S)
    return []


async def fetch_all_candles_for_symbol(
    exchange: ccxt.Exchange,
    unified_symbol: str,
    timeframe: str,
    lookback_days: int,
) -> list[dict]:
    """Fetch all historical candles for one symbol using backward pagination."""
    market_id = _exchange_market_id(unified_symbol, exchange)
    since_dt = datetime.now(UTC) - timedelta(days=lookback_days)
    since_ms = int(since_dt.timestamp() * 1000)

    all_candles: list[list] = []
    current_since = since_ms
    ms_per_candle = _timeframe_to_ms(timeframe)

    logger.info("Fetching {} ({}) from {}", unified_symbol, market_id, since_dt.date())

    while True:
        batch = await fetch_candles_range(exchange, market_id, timeframe, current_since)
        if not batch:
            break

        existing_tss = {c[0] for c in all_candles}
        new_c = [c for c in batch if c[0] not in existing_tss]
        all_candles.extend(new_c)

        logger.debug(
            "  {}: {} candles (new={}), ts=[{}..{}]",
            unified_symbol, len(batch), len(new_c),
            _ts(batch[0][0]), _ts(batch[-1][0]),
        )

        if len(batch) < MAX_PER_REQUEST:
            logger.info("  {}: caught up ({})", unified_symbol, len(all_candles))
            break

        last_ts = batch[-1][0]
        current_since = last_ts + ms_per_candle
        if current_since > _now_ms() + ms_per_candle:
            logger.warning("  {}: since past present, stopping", unified_symbol)
            break

        await asyncio.sleep(1.0)

    return [
        {
            "symbol": unified_symbol,
            "timeframe": timeframe,
            "ts": datetime.fromtimestamp(c[0] / 1000, tz=UTC),
            "open": str(Decimal(str(c[1]))),
            "high": str(Decimal(str(c[2]))),
            "low": str(Decimal(str(c[3]))),
            "close": str(Decimal(str(c[4]))),
            "volume": str(Decimal(str(c[5]))),
        }
        for c in all_candles
    ]


# ── DB helpers ─────────────────────────────────────────────


async def bulk_upsert_candles(
    conn: asyncpg.Connection,
    candles: list[dict],
    batch_size: int = 500,
) -> int:
    """Upsert candles in batches. Returns inserted count."""
    if not candles:
        return 0

    inserted = 0
    for i in range(0, len(candles), batch_size):
        batch = candles[i : i + batch_size]
        values = []
        params: list[Any] = []
        for j, c in enumerate(batch):
            off = j * 9
            values.append(
                f"(${off + 1}::text, ${off + 2}::text, ${off + 3}::timestamptz, "
                f"${off + 4}::numeric, ${off + 5}::numeric, "
                f"${off + 6}::numeric, ${off + 7}::numeric, ${off + 8}::numeric, ${off + 9}::numeric)"
            )
            params.extend([
                c["symbol"], c["timeframe"], c["ts"],
                c["open"], c["high"], c["low"], c["close"], c["volume"],
            ])

        query = f"""
            INSERT INTO historical_candles (symbol, timeframe, ts, open, high, low, close, volume)
            VALUES {', '.join(values)}
            ON CONFLICT (symbol, timeframe, ts) DO NOTHING
        """
        r = await conn.execute(query, *params)
        inserted += int(r.split()[-1])

    return inserted


# ── Utilities ──────────────────────────────────────────────


def _timeframe_to_ms(tf: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    num = int(tf[:-1])
    return num * units.get(tf[-1], 60_000)


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── Main ───────────────────────────────────────────────────


async def main() -> None:
    args = parse_args()

    if args.symbols.upper() == "ALL":
        try:
            from app.core.config import get_settings
            raw_symbols = get_settings().symbols
        except ImportError:
            raw_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    else:
        raw_symbols = [s.strip() for s in args.symbols.split(",")]

    symbols = [_standardize_symbol(s) for s in raw_symbols]

    if args.db_url:
        db_url = args.db_url
    else:
        try:
            from app.core.config import get_settings
            db_url = get_settings().postgres_url.replace("+asyncpg", "")
        except ImportError:
            db_url = "postgresql://karsa:karsa@db:5432/karsa"

    logger.info("Connecting to DB: {}", db_url)
    conn = await asyncpg.connect(db_url)
    logger.info("Ingesting {} symbols ({} timeframe, {} days)", len(symbols), args.timeframe, args.days)

    exchange = _make_exchange(args.exchange)
    await exchange.load_markets()

    total = 0
    try:
        for sym in symbols:
            candles = await fetch_all_candles_for_symbol(exchange, sym, args.timeframe, args.days)
            if candles:
                ins = await bulk_upsert_candles(conn, candles)
                total += ins
                logger.info("{}: {} inserted (total: {})", sym, ins, total)
            else:
                logger.warning("{}: no candles", sym)
    finally:
        await exchange.close()
        await conn.close()

    logger.info("Ingestion complete — {} candles across {} symbols", total, len(symbols))


if __name__ == "__main__":
    asyncio.run(main())
