"""Dynamic Universe Scorer — score symbols by Volume + Momentum + Squeeze + Overextension.

Selects top N symbols above threshold, respecting sector diversity cap.
Refreshes every 4 hours. Falls back to static config list if empty.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from app.alpha.ta_tools import calculate_bollinger_bands
from app.core.redis_client import RedisClient
from app.data.ohlcv_fetcher import OHLCVFetcher
from app.data.sector_mapping import get_sector

# Scoring weights
VOLUME_MAX = Decimal("30")      # 0-30
MOMENTUM_MAX = Decimal("40")    # 0-40
SQUEEZE_MAX = Decimal("30")     # 0-30
OVEREXTENSION_MAX = Decimal("40")  # penalty -40 to 0

# Selection thresholds
DEFAULT_TOP_N = 40
DEFAULT_MIN_SCORE = Decimal("40")
DEFAULT_MAX_PER_SECTOR = 3
OVEREXTENSION_THRESHOLD = Decimal("0.30")  # 30% 24h move


class UniverseScorer:
    """Score and select tradeable symbols from configured universe.

    ponytail: simple linear scoring, no ML. Scores computed on-demand.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        ohlcv_fetcher: OHLCVFetcher,
        symbols: List[str],
        top_n: int = DEFAULT_TOP_N,
        min_score: Decimal = DEFAULT_MIN_SCORE,
        max_per_sector: int = DEFAULT_MAX_PER_SECTOR,
    ) -> None:
        self.redis = redis_client
        self.fetcher = ohlcv_fetcher
        self.symbols = symbols
        self.top_n = top_n
        self.min_score = min_score
        self.max_per_sector = max_per_sector

    async def score_symbol(self, symbol: str) -> Optional[Dict]:
        """Score a single symbol. Returns dict or None if data unavailable."""
        state = await self.redis.get_global_state(symbol)
        if not state:
            return None

        total_volume = Decimal(str(state.get("total_volume", "0")))
        if total_volume <= 0:
            return None
        volume_score = min(total_volume / Decimal("1000000000") * VOLUME_MAX, VOLUME_MAX)

        try:
            candles = await self.fetcher.fetch(symbol, timeframe="1h", limit=25)
        except Exception as e:
            logger.debug(f"Universe scorer OHLCV failed for {symbol}: {e}")
            return None

        if not candles or len(candles) < 21:
            return None

        closes = [Decimal(str(c[4])) for c in candles]

        # Momentum: 1H price change %
        if len(closes) >= 2:
            price_change_pct = (closes[-1] - closes[-2]) / closes[-2]
            momentum_score = min(abs(price_change_pct) * Decimal("1000"), MOMENTUM_MAX)
            if price_change_pct < 0:
                momentum_score *= Decimal("0.7")
        else:
            momentum_score = Decimal("0")

        # Overextension penalty: >30% move in 24h
        if len(closes) >= 21:
            price_24h_ago = closes[-21]
            if price_24h_ago > 0:
                move_24h = abs((closes[-1] - price_24h_ago) / price_24h_ago)
                if move_24h > OVEREXTENSION_THRESHOLD:
                    overextension_penalty = -min(
                        (move_24h - OVEREXTENSION_THRESHOLD) * Decimal("200"), OVEREXTENSION_MAX
                    )
                else:
                    overextension_penalty = Decimal("0")
            else:
                overextension_penalty = Decimal("0")
        else:
            overextension_penalty = Decimal("0")

        # Squeeze: BB width narrowing
        bb = calculate_bollinger_bands(closes, period=20)
        if bb:
            upper, middle, lower = bb
            if middle > 0:
                bb_width = (upper - lower) / middle
                squeeze_score = max(SQUEEZE_MAX - bb_width * Decimal("400"), Decimal("0"))
            else:
                squeeze_score = Decimal("0")
        else:
            squeeze_score = Decimal("0")

        total = volume_score + momentum_score + overextension_penalty + squeeze_score

        return {
            "symbol": symbol,
            "volume_score": round(volume_score, 2),
            "momentum_score": round(momentum_score, 2),
            "overextension_penalty": round(overextension_penalty, 2),
            "squeeze_score": round(squeeze_score, 2),
            "total_score": round(total, 2),
            "sector": get_sector(symbol),
        }

    async def score_all(self) -> List[Dict]:
        """Score all configured symbols."""
        results = []
        for symbol in self.symbols:
            score = await self.score_symbol(symbol)
            if score:
                results.append(score)
        return results

    async def select(self) -> List[Dict]:
        """Score, rank, filter by sector cap, return top N above threshold."""
        all_scores = await self.score_all()
        all_scores.sort(key=lambda x: x["total_score"], reverse=True)

        sector_counts: Dict[str, int] = {}
        selected = []
        for candidate in all_scores:
            if candidate["total_score"] < self.min_score:
                continue
            sector = candidate["sector"]
            count = sector_counts.get(sector, 0)
            if count >= self.max_per_sector:
                continue
            sector_counts[sector] = count + 1
            selected.append(candidate)
            if len(selected) >= self.top_n:
                break

        return selected

    async def refresh(self, config_symbols: List[str]) -> List[str]:
        """Run full selection, write to Redis, return active symbol list."""
        selected = await self.select()
        symbols = [s["symbol"] for s in selected]

        if not symbols:
            logger.warning("Universe scorer returned 0 — falling back to static config list")
            symbols = config_symbols[:self.top_n]
            selected = [{"symbol": s, "total_score": 0, "sector": get_sector(s)} for s in symbols]

        payload = {
            "symbols": symbols,
            "scores": {s["symbol"]: s["total_score"] for s in selected},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self.redis.redis.set("system:universe:symbols", json.dumps(payload, default=str))
            logger.info(f"Universe refreshed: {len(symbols)} symbols active")
        except Exception as e:
            logger.error(f"Universe Redis write failed: {e}")

        return symbols
