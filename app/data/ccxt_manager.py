"""Global Data Engine — CCXT Pro WebSocket connections."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import ccxt.pro as ccxt_pro
from loguru import logger

from app.core import metrics


class CCXTManager:
    """Manages WebSocket connections to multiple exchanges via CCXT Pro."""

    def __init__(self) -> None:
        logger.debug("CCXTManager.__init__: entering")
        self.exchanges: dict[str, ccxt_pro.Exchange] = {}
        self.markets_loaded: list[str] = []  # exchanges with successfully loaded markets
        self.last_update: dict[str, datetime] = {}
        self.stale_threshold_seconds: int = 15
        logger.debug("CCXTManager.__init__: returning")

    async def start(self, testnet: bool = False) -> None:
        """Initialize exchanges, load markets, and start WebSocket streams.
        Callers: main.py (passes settings.bybit_testnet). No schema change."""
        logger.debug("start: entering")
        # Binance — spot only
        binance = ccxt_pro.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "defaultSubType": "spot",
                "fetchMarkets": ["spot"],
            },
        })
        self.exchanges["binance"] = binance

        # OKX — spot only
        okx = ccxt_pro.okx({
            "enableRateLimit": True,
        })
        okx.options["defaultType"] = "spot"
        self.exchanges["okx"] = okx

        # Bybit — USDT perpetual
        bybit = ccxt_pro.bybit({
            "enableRateLimit": True,
        })
        bybit.options["defaultType"] = "swap"
        if testnet:
            bybit.set_sandbox_mode(True)
            logger.info("Bybit CCXT set to sandbox/testnet mode")
        self.exchanges["bybit"] = bybit

        logger.info(f"Initialized exchanges: {list(self.exchanges.keys())}")

        # Load markets for all exchanges (needed for symbol validation)
        # Retries needed — Gluetun VPN tunnel can be slow/unstable for HTTPS REST
        # Non-fatal: if all retries fail, skip that exchange for validation
        max_retries = 3
        for exchange_id, exchange in self.exchanges.items():
            loaded = False
            for attempt in range(1, max_retries + 1):
                try:
                    await exchange.load_markets()
                    self.markets_loaded.append(exchange_id)
                    logger.info(f"Loaded {len(exchange.markets)} markets from {exchange_id}")
                    loaded = True
                    break
                except Exception as e:
                    if attempt == max_retries:
                        logger.error(f"Failed to load markets from {exchange_id} after {max_retries} attempts: {e}")
                        break
                    wait = 2 ** attempt
                    logger.warning(f"load_markets({exchange_id}) attempt {attempt}/{max_retries} failed: {e}, retrying in {wait}s")
                    await asyncio.sleep(wait)
            if not loaded:
                logger.warning(f"Symbol validation will skip {exchange_id} — markets not loaded")

        logger.debug("start: returning None")

    def _resolve_symbol(self, symbol: str, exchange_id: str) -> str:
        """Resolve config symbol (BTC/USDT) to CCXT market symbol (BTC/USDT or BTC/USDT:USDT)."""
        exchange = self.exchanges.get(exchange_id)
        if not exchange:
            return symbol
        if symbol in exchange.markets:
            return symbol
        swap_symbol = f"{symbol}:USDT"
        if swap_symbol in exchange.markets:
            return swap_symbol
        return symbol

    def get_bybit_universe(self, target_symbols: list[str]) -> list[str]:
        """Return symbols present on Bybit (authoritative trading venue).
        Binance/OKX absence does NOT exclude a symbol — they are reference data only.
        """
        if "bybit" not in self.markets_loaded:
            logger.warning("Bybit markets not loaded — cannot validate. Returning all config symbols.")
            return target_symbols
        valid = []
        for symbol in target_symbols:
            resolved = self._resolve_symbol(symbol, "bybit")
            if resolved in self.exchanges["bybit"].markets:
                valid.append(symbol)
            else:
                logger.debug(f"Symbol {symbol} not on Bybit, skipping")
        return valid

    async def fetch_bybit_perps(
        self,
        min_volume_usd: float = 5_000_000,
        top_n: int = 80,
    ) -> list[str]:
        """Dynamic symbol discovery: fetch all Bybit USDT perpetuals, filter by 24h volume.

        Returns top_n symbols sorted by 24h quote volume (descending), filtered by
        min_volume_usd floor. Symbols returned in CCXT format (e.g. 'BTC/USDT').
        Falls back to empty list on failure.
        """
        bybit = self.exchanges.get("bybit")
        if not bybit or "bybit" not in self.markets_loaded:
            logger.warning("fetch_bybit_perps: Bybit not available")
            return []

        try:
            tickers = await bybit.fetch_tickers(params={"category": "linear"})
        except Exception as e:
            logger.error(f"fetch_bybit_perps: fetch_tickers failed: {e}")
            return []

        candidates: list[tuple[str, float]] = []
        for symbol, ticker in tickers.items():
            # Only USDT perpetuals (BTC/USDT:USDT format)
            if not symbol.endswith(":USDT"):
                continue
            market = bybit.markets.get(symbol)
            if not market or not market.get("swap"):
                continue
            vol_usd = float(ticker.get("quoteVolume") or 0)
            if vol_usd < min_volume_usd:
                continue
            # Normalize to config format: BTC/USDT:USDT -> BTC/USDT
            base = symbol.split(":")[0]
            candidates.append((base, vol_usd))

        candidates.sort(key=lambda x: x[1], reverse=True)
        result = [s for s, _ in candidates[:top_n]]
        logger.info(
            f"fetch_bybit_perps: {len(candidates)} above ${min_volume_usd:,.0f} volume, "
            f"selected top {len(result)}"
        )
        return result

    def get_reference_symbols(self, target_symbols: list[str], exchange_id: str) -> list[str]:
        """Return subset of symbols that also exist on a reference exchange (Binance/OKX).
        Used by the data engine to know which streams to open for cross-exchange analysis.
        """
        if exchange_id not in self.markets_loaded:
            return []
        valid = []
        for symbol in target_symbols:
            resolved = self._resolve_symbol(symbol, exchange_id)
            if resolved in self.exchanges[exchange_id].markets:
                valid.append(symbol)
        return valid

    async def watch_orderbook(self, symbol: str, exchange_id: str) -> dict:
        """Watch L2 orderbook for a symbol on a specific exchange."""
        logger.debug(f"watch_orderbook: entering symbol={symbol} exchange_id={exchange_id}")
        exchange = self.exchanges.get(exchange_id)
        if not exchange:
            raise ValueError(f"Unknown exchange: {exchange_id}")

        try:
            target = self._resolve_symbol(symbol, exchange_id)
            orderbook = await exchange.watch_order_book(target)
            self.last_update[exchange_id] = datetime.now(UTC)
            logger.debug("watch_orderbook: returning dict")
            return orderbook
        except Exception as e:
            metrics.ws_disconnects.labels(exchange=exchange_id).inc()
            logger.error(f"WebSocket error on {exchange_id}: {e}")
            # Force close stale WebSocket so next watch triggers fresh reconnect
            try:
                if hasattr(exchange, 'ws') and exchange.ws:
                    await exchange.ws.close()
                    logger.info(f"Force-closed stale WS on {exchange_id}")
            except Exception:
                pass
            raise

    async def watch_trades(self, symbol: str, exchange_id: str) -> list:
        """Watch trades for a symbol on a specific exchange."""
        logger.debug(f"watch_trades: entering symbol={symbol} exchange_id={exchange_id}")
        exchange = self.exchanges.get(exchange_id)
        if not exchange:
            raise ValueError(f"Unknown exchange: {exchange_id}")

        try:
            target = self._resolve_symbol(symbol, exchange_id)
            trades = await exchange.watch_trades(target)
            self.last_update[exchange_id] = datetime.now(UTC)
            logger.debug("watch_trades: returning list")
            return trades
        except Exception as e:
            metrics.ws_disconnects.labels(exchange=exchange_id).inc()
            logger.error(f"WebSocket error on {exchange_id}: {e}")
            # Force close stale WebSocket so next watch triggers fresh reconnect
            try:
                if hasattr(exchange, 'ws') and exchange.ws:
                    await exchange.ws.close()
                    logger.info(f"Force-closed stale WS on {exchange_id}")
            except Exception:
                pass
            raise

    def is_stale(self, exchange_id: str) -> bool:
        """Check if an exchange feed is stale (no update >15s)."""
        logger.debug(f"is_stale: entering exchange_id={exchange_id}")
        last = self.last_update.get(exchange_id)
        if not last:
            metrics.exchange_status.labels(exchange=exchange_id).set(1)
            logger.debug("is_stale: returning True (no last update)")
            return True

        elapsed = (datetime.now(UTC) - last).total_seconds()
        result = elapsed > self.stale_threshold_seconds
        metrics.exchange_status.labels(exchange=exchange_id).set(1 if result else 0)
        logger.debug(f"is_stale: returning {result}")
        return result

    async def close(self) -> None:
        """Close all exchange connections."""
        logger.debug("close: entering")
        for exchange_id, exchange in self.exchanges.items():
            try:
                await exchange.close()
                logger.info(f"Closed {exchange_id} connection")
            except Exception as e:
                logger.error(f"Error closing {exchange_id}: {e}")
        logger.debug("close: returning None")
