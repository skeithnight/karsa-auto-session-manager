"""Micro Data Loader — fetches high-resolution data for backtesting.

Downloads and caches 1m/5m OHLCV and historical funding rates from Bybit.
Saves data locally as CSV (or Parquet if pyarrow is installed) to prevent
exchange rate limits during repeated backtest and optimization runs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger

CACHE_DIR = Path("data_cache")


class MicroDataLoader:
    """Fetches and caches high-resolution historical data for backtesting."""

    def __init__(
        self,
        exchange_id: str = "bybit",
        cache_dir: Path = CACHE_DIR,
    ) -> None:
        self.exchange_id = exchange_id
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._exchange_class = getattr(ccxt, exchange_id)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 1000,
        since_ms: int | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV data. Returns cached data if available."""
        safe_symbol = symbol.replace("/", "_")
        cache_file = self.cache_dir / f"{self.exchange_id}_{safe_symbol}_{timeframe}.csv"

        if cache_file.exists() and not force_refresh:
            logger.info("MicroDataLoader: Loading cached %s data for %s", timeframe, symbol)
            df = pd.read_csv(cache_file)
            return df

        logger.info("MicroDataLoader: Fetching %s data for %s from %s", timeframe, symbol, self.exchange_id)
        exchange = self._exchange_class({"options": {"defaultType": "swap"}})
        
        try:
            await exchange.load_markets()
            
            # Fetch all data up to limit (pagination if needed)
            all_ohlcv = []
            current_since = since_ms
            remaining = limit
            
            while remaining > 0:
                fetch_limit = min(remaining, 1000)
                ohlcv = await exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe, limit=fetch_limit, since=current_since
                )
                if not ohlcv:
                    break
                    
                all_ohlcv.extend(ohlcv)
                remaining -= len(ohlcv)
                current_since = ohlcv[-1][0] + 1  # Next timestamp
                
                # Rate limit safety
                await asyncio.sleep(exchange.rateLimit / 1000)

            df = pd.DataFrame(
                all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df.to_csv(cache_file, index=False)
            logger.info("MicroDataLoader: Saved %d rows to %s", len(df), cache_file)
            return df

        finally:
            await exchange.close()

    async def fetch_historical_funding(
        self,
        symbol: str,
        limit: int = 1000,
        since_ms: int | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Fetch historical funding rates."""
        safe_symbol = symbol.replace("/", "_")
        cache_file = self.cache_dir / f"{self.exchange_id}_{safe_symbol}_funding.csv"

        if cache_file.exists() and not force_refresh:
            logger.info("MicroDataLoader: Loading cached funding data for %s", symbol)
            return pd.read_csv(cache_file)

        logger.info("MicroDataLoader: Fetching historical funding for %s", symbol)
        exchange = self._exchange_class({"options": {"defaultType": "swap"}})
        
        try:
            await exchange.load_markets()
            
            funding_rates = []
            current_since = since_ms
            remaining = limit
            
            while remaining > 0:
                fetch_limit = min(remaining, 200)
                rates = await exchange.fetch_funding_rate_history(
                    symbol, since=current_since, limit=fetch_limit
                )
                if not rates:
                    break
                    
                funding_rates.extend(rates)
                remaining -= len(rates)
                current_since = rates[-1]['timestamp'] + 1
                
                await asyncio.sleep(exchange.rateLimit / 1000)

            # Extract relevant fields
            parsed_rates = [
                {"timestamp": r["timestamp"], "fundingRate": r["fundingRate"]}
                for r in funding_rates
            ]
            
            df = pd.DataFrame(parsed_rates)
            df.to_csv(cache_file, index=False)
            logger.info("MicroDataLoader: Saved %d funding rows to %s", len(df), cache_file)
            return df

        finally:
            await exchange.close()
