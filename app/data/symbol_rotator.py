"""Dynamic Symbol Rotator — auto-discovers trending symbols, drops RANGE ones.

Continuously monitors Bybit for symbols with clear trends (TREND_BULL/TREND_BEAR)
and replaces symbols that have been in RANGE regime for too long.

Redis Keys:
    system:universe:symbols — active universe (JSON: {symbols: [...], updated: ts})
    system:regime:{symbol} — regime per symbol (written by RegimeClassifier)
    system:rotator:log — rotation events (Redis list, max 100)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import ccxt.async_support as ccxt
import numpy as np

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────

# Minimum trend strength to consider a symbol trending
MIN_TREND_PCT = 3.0  # price vs 50-period SMA
MIN_MOMENTUM_PCT = 5.0  # 20-period momentum

# Universe sizing
MIN_SYMBOLS = 4
MAX_SYMBOLS = 12

# How often to scan for new symbols (seconds)
SCAN_INTERVAL = 3600  # 1 hour

# How long a symbol can stay in RANGE before rotation (seconds)
RANGE_EXPIRY = 7200  # 2 hours


@dataclass
class SymbolCandidate:
    """A symbol discovered by the scanner."""
    symbol: str
    regime: str  # TREND_BULL or TREND_BEAR
    trend_pct: float
    momentum_pct: float
    atr_pct: float
    volume_usd: float
    score: float  # composite score for ranking


class SymbolRotator:
    """Dynamic symbol rotation — discovers trending symbols, drops RANGE ones."""

    def __init__(
        self,
        redis_client,
        initial_symbols: list[str] | None = None,
        min_symbols: int = MIN_SYMBOLS,
        max_symbols: int = MAX_SYMBOLS,
        range_expiry_s: float = RANGE_EXPIRY,
    ) -> None:
        self._redis = redis_client
        self._symbols = list(initial_symbols or [])
        self._min = min_symbols
        self._max = max_symbols
        self._range_expiry = range_expiry_s
        self._range_tracker: dict[str, float] = {}  # symbol -> first RANGE timestamp
        self._last_scan: float = 0.0
        self._exchange: ccxt.bybit | None = None

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    async def start(self) -> None:
        """Initialize exchange session."""
        self._exchange = ccxt.bybit({"enableRateLimit": True})
        await self._exchange.load_markets()
        logger.info("SymbolRotator: initialized with %d symbols", len(self._symbols))

    async def stop(self) -> None:
        if self._exchange:
            await self._exchange.close()

    async def rotation_loop(self, interval_s: int = SCAN_INTERVAL) -> None:
        """Main rotation loop. Runs until cancelled."""
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self._rotate()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("SymbolRotator error: %s", e)
                await asyncio.sleep(60)

    async def _rotate(self) -> None:
        """Check regimes, drop RANGE symbols, add trending ones."""
        now = time.time()

        # 1. Check which symbols are RANGE
        range_symbols = []
        for sym in list(self._symbols):
            regime = await self._get_regime(sym)
            if regime == "RANGE":
                if sym not in self._range_tracker:
                    self._range_tracker[sym] = now
                elif now - self._range_tracker[sym] > self._range_expiry:
                    range_symbols.append(sym)
            else:
                # Not RANGE anymore, reset tracker
                self._range_tracker.pop(sym, None)

        if not range_symbols:
            logger.debug("SymbolRotator: no RANGE symbols to rotate")
            return

        # 2. Find trending replacements
        candidates = await self._scan_trending()

        # 3. Filter out symbols already in universe
        existing = set(self._symbols)
        candidates = [c for c in candidates if c.symbol not in existing]

        # 4. Replace RANGE symbols with top candidates
        replacements = []
        for sym in range_symbols:
            if candidates:
                candidate = candidates.pop(0)
                replacements.append((sym, candidate))
                self._symbols.remove(sym)
                self._symbols.append(candidate.symbol)
                self._range_tracker.pop(sym, None)
                logger.info(
                    "SymbolRotator: rotated %s (RANGE %.0fm) → %s (%s, trend=%+.1f%%)",
                    sym,
                    (now - self._range_tracker.get(sym, now)) / 60,
                    candidate.symbol,
                    candidate.regime,
                    candidate.trend_pct,
                )

        if replacements:
            await self._publish_universe()
            await self._log_rotation(replacements)

    async def _get_regime(self, symbol: str) -> str | None:
        """Get current regime for a symbol from Redis."""
        try:
            regime = await self._redis.get(f"system:regime:{symbol}")
            if regime:
                return regime if isinstance(regime, str) else regime.decode()
        except Exception:
            pass
        return None

    async def _scan_trending(self) -> list[SymbolCandidate]:
        """Scan Bybit for trending symbols."""
        if not self._exchange:
            return []

        candidates = []
        try:
            swaps = [
                s
                for s, m in self._exchange.markets.items()
                if m.get("swap") and ":USDT" in s
            ]

            for sym in swaps[:200]:  # Check top 200 by volume
                try:
                    ohlcv = await self._exchange.fetch_ohlcv(sym, "1h", limit=100)
                    if len(ohlcv) < 50:
                        continue

                    closes = np.array([c[4] for c in ohlcv])
                    volumes = np.array([c[5] for c in ohlcv])
                    current = closes[-1]

                    # Trend: price vs 50-period SMA
                    sma50 = np.mean(closes[-50:])
                    trend_pct = ((current - sma50) / sma50) * 100

                    # Momentum: 20-period return
                    mom_pct = ((current - closes[-20]) / closes[-20]) * 100

                    # ATR for volatility filter
                    highs = [c[2] for c in ohlcv]
                    lows = [c[3] for c in ohlcv]
                    tr = [
                        max(
                            highs[i] - lows[i],
                            abs(highs[i] - closes[i - 1]),
                            abs(lows[i] - closes[i - 1]),
                        )
                        for i in range(1, len(ohlcv))
                    ]
                    atr = np.mean(tr[-14:])
                    atr_pct = (atr / current) * 100

                    # Volume in USD (approximate)
                    vol_usd = float(np.mean(volumes[-20:]) * current)

                    # Filter: must be trending
                    if abs(trend_pct) < MIN_TREND_PCT or abs(mom_pct) < MIN_MOMENTUM_PCT:
                        continue

                    # Filter: minimum volatility (avoid dead tokens)
                    if atr_pct < 0.5:
                        continue

                    # Filter: minimum volume
                    if vol_usd < 100_000:
                        continue

                    direction = "TREND_BULL" if trend_pct > 0 else "TREND_BEAR"

                    # Composite score: trend strength + momentum + volume
                    score = abs(trend_pct) * 0.4 + abs(mom_pct) * 0.3 + min(vol_usd / 1_000_000, 10) * 0.3

                    name = sym.split(":")[0]
                    candidates.append(
                        SymbolCandidate(
                            symbol=name,
                            regime=direction,
                            trend_pct=trend_pct,
                            momentum_pct=mom_pct,
                            atr_pct=atr_pct,
                            volume_usd=vol_usd,
                            score=score,
                        )
                    )
                except Exception:
                    continue

        except Exception as e:
            logger.error("SymbolRotator scan error: %s", e)

        # Sort by score
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    async def _publish_universe(self) -> None:
        """Write current universe to Redis."""
        data = {
            "symbols": self._symbols,
            "updated": time.time(),
            "count": len(self._symbols),
        }
        await self._redis.set("system:universe:symbols", json.dumps(data))
        logger.info("SymbolRotator: published universe (%d symbols)", len(self._symbols))

    async def _log_rotation(self, replacements: list[tuple[str, SymbolCandidate]]) -> None:
        """Log rotation events to Redis."""
        for old_sym, candidate in replacements:
            entry = json.dumps({
                "timestamp": time.time(),
                "removed": old_sym,
                "added": candidate.symbol,
                "regime": candidate.regime,
                "trend_pct": candidate.trend_pct,
                "score": candidate.score,
            })
            await self._redis.lpush("system:rotator:log", entry)
        # Trim to last 100 entries
        await self._redis.ltrim("system:rotator:log", 0, 99)
