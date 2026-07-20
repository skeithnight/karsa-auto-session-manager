"""Dynamic Universe Scanner — volume + ATR discovery from Bybit tickers.

Periodically fetches all Bybit USDT perpetual tickers, filters by 24h volume,
computes ATR(14) for volatility, and ranks by composite score. Writes active
symbol list to Redis for the DataEngine to consume.

Redis keys (shared with UniverseScorer for compatibility):
  system:universe:symbols         — {symbols, scores, updated_at}
  system:universe:scanner:status  — {last_refresh, symbol_count, ...}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt
from loguru import logger

from app.core import metrics

REDIS_UNIVERSE_KEY = "system:universe:symbols"
REDIS_SCANNER_STATUS_KEY = "system:universe:scanner:status"

DEFAULT_TOP_N = 40
DEFAULT_MIN_VOLUME_USD = 5_000_000.0
DEFAULT_REFRESH_INTERVAL_S = 4 * 3600  # 4 hours
ATR_PERIOD = 14
ATR_CANDLE_LIMIT = 50
ATR_CAP_CANDIDATES = 80  # max symbols to fetch OHLCV for


def compute_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ATR_PERIOD,
) -> float:
    """Wilder-smoothed ATR over OHLC arrays. Returns 0.0 on insufficient data."""
    n = len(closes)
    if n < period + 1:
        return 0.0

    prev_closes = [closes[0]] + closes[:-1]
    tr_values = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - prev_closes[i]),
            abs(lows[i] - prev_closes[i]),
        )
        for i in range(n)
    ]

    atr = sum(tr_values[1 : period + 1]) / period
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr_values[i]) / period

    return atr


class DynamicUniverseScanner:
    """Fetches all Bybit USDT perpetual tickers, filters and ranks by volume x ATR.

    Writes active symbol list to Redis. Run via ``refresh()`` on a timer.
    """

    def __init__(  # noqa: PLR0913
        self,
        redis_client: Any,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
        top_n: int = DEFAULT_TOP_N,
        min_volume_usd: float = DEFAULT_MIN_VOLUME_USD,
        refresh_interval_s: int = DEFAULT_REFRESH_INTERVAL_S,
        fallback_symbols: list[str] | None = None,
    ) -> None:
        self._redis = redis_client
        self._top_n = top_n
        self._min_vol = min_volume_usd
        self._interval = refresh_interval_s
        self._fallback = fallback_symbols or []
        self._session: ccxt.bybit | None = None
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._running = False

        # Last result cache
        self.symbols: list[str] = []
        self.scores: dict[str, float] = {}

    async def start(self) -> None:
        """Main loop — refreshes on interval until stop()."""
        self._running = True
        self._session = ccxt.bybit({
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "options": {"defaultType": "swap"},
        })
        if self._testnet:
            self._session.set_sandbox_mode(True)

        logger.info(
            "UniverseScanner: starting top_n=%d min_vol=$%.0f interval=%ds",
            self._top_n, self._min_vol, self._interval,
        )

        while self._running:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("UniverseScanner: refresh failed")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Stop the scanner loop."""
        self._running = False
        if self._session:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None
        logger.info("UniverseScanner: stopped")

    async def refresh(self) -> list[str]:
        """Run one scan cycle: fetch tickers, filter, rank, write to Redis.

        Returns the list of selected symbols.
        """
        if not self._session:
            logger.warning("UniverseScanner: no session, skipping refresh")
            return self.symbols

        # 1. Fetch all Bybit USDT perpetual tickers
        try:
            tickers = await self._session.fetch_tickers(params={"category": "linear"})
        except Exception as exc:
            logger.error("UniverseScanner: fetch_tickers failed: %s", exc)
            return self._fallback_or_existing()

        # 2. Filter by volume
        candidates: list[dict[str, Any]] = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith(":USDT"):
                continue
            market = self._session.markets.get(symbol)
            if not market or not market.get("swap"):
                continue
            vol_usd = float(ticker.get("quoteVolume") or 0)
            if vol_usd < self._min_vol:
                continue
            base = symbol.split(":")[0]
            percentage = float(ticker.get("percentage") or 0)
            candidates.append({
                "symbol": base,
                "volume_usd": vol_usd,
                "percentage": percentage,
            })

        if not candidates:
            logger.warning("UniverseScanner: no candidates above volume threshold")
            return self._fallback_or_existing()

        logger.info(
            "UniverseScanner: %d candidates above $%.0f volume",
            len(candidates), self._min_vol,
        )

        # 3. Compute ATR for top candidates (cap to limit API calls)
        sort_by_vol = sorted(candidates, key=lambda c: c["volume_usd"], reverse=True)
        for cand in sort_by_vol[:ATR_CAP_CANDIDATES]:
            try:
                cand["atr"] = await self._compute_symbol_atr(cand["symbol"])
            except Exception:
                logger.debug("UniverseScanner: ATR failed for %s", cand["symbol"])
                cand["atr"] = 0.0
        for cand in sort_by_vol[ATR_CAP_CANDIDATES:]:
            cand["atr"] = 0.0

        # 4. Compute composite score: 40% volume + 30% ATR + 30% gainer/momentum
        max_vol = max(c["volume_usd"] for c in candidates) or 1.0
        max_atr = max(c.get("atr", 0) for c in candidates) or 1.0
        max_pct = max(abs(c.get("percentage", 0)) for c in candidates) or 1.0
        
        for cand in candidates:
            norm_vol = cand["volume_usd"] / max_vol
            norm_atr = cand.get("atr", 0) / max_atr if max_atr > 0 else 0.0
            
            # Use absolute percentage for momentum, but penalize negative moves slightly
            pct = cand.get("percentage", 0)
            abs_pct = abs(pct)
            norm_pct = abs_pct / max_pct if max_pct > 0 else 0.0
            if pct < 0:
                norm_pct *= 0.7  # Prefer gainers over losers
                
            cand["score"] = norm_vol * 0.4 + norm_atr * 0.3 + norm_pct * 0.3

        # 5. Sort by composite score, take top N
        candidates.sort(key=lambda c: c["score"], reverse=True)
        selected = candidates[: self._top_n]

        self.symbols = [c["symbol"] for c in selected]
        self.scores = {c["symbol"]: round(c["score"], 4) for c in selected}

        # 6. Write to Redis
        await self._write_redis()

        logger.info(
            "UniverseScanner: refreshed — %d symbols, top=%s (score=%.3f)",
            len(self.symbols),
            self.symbols[0] if self.symbols else "none",
            self.scores.get(self.symbols[0], 0) if self.symbols else 0,
        )
        return self.symbols

    async def _compute_symbol_atr(self, symbol: str) -> float:
        """Fetch 50 candles and compute ATR(14)."""
        if not self._session:
            return 0.0
        candles = await self._session.fetch_ohlcv(symbol, "1h", limit=ATR_CANDLE_LIMIT)
        if len(candles) < ATR_PERIOD + 1:
            return 0.0
        highs = [c[2] for c in candles]
        lows = [c[3] for c in candles]
        closes = [c[4] for c in candles]
        return compute_atr(highs, lows, closes)

    async def _write_redis(self) -> None:
        """Write universe list and scanner status to Redis."""
        payload = {
            "symbols": self.symbols,
            "scores": self.scores,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        status_data = {
            "last_refresh": datetime.now(UTC).isoformat(),
            "symbol_count": len(self.symbols),
            "top_symbol": self.symbols[0] if self.symbols else "",
        }
        try:
            await self._redis.set(REDIS_UNIVERSE_KEY, json.dumps(payload, default=str))
            await self._redis.set(REDIS_SCANNER_STATUS_KEY, json.dumps(status_data, default=str))
            metrics.universe_size.set(len(self.symbols))
        except Exception:
            logger.exception("UniverseScanner: Redis write failed")

    def _fallback_or_existing(self) -> list[str]:
        """Return cached symbols or fallback to config list."""
        if self.symbols:
            logger.info("UniverseScanner: keeping %d cached symbols", len(self.symbols))
            return list(self.symbols)
        if self._fallback:
            logger.info("UniverseScanner: using %d fallback config symbols", len(self._fallback))
            self.symbols = list(self._fallback)
            return list(self.symbols)
        return []

    def get_active_symbols(self) -> list[str]:
        """Return the current active symbol list."""
        return list(self.symbols)
