"""Pre-Entry Analyst — AI-enhanced signal validation.

Runs AFTER deterministic signal generation, BEFORE order placement.
Only activates in ambiguous confidence zone (0.55-0.85).
Fetches OHLCV, computes TA indicators, asks AI for second opinion.
Result blends with deterministic confidence: 50/50 weight.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from loguru import logger

from app.core import metrics
from app.alpha.ta_tools import (
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_macd,
    calculate_atr,
    calculate_ema,
)
from app.core.ai_client import AIClient
from app.data.ohlcv_fetcher import OHLCVFetcher


@dataclass
class AnalystResult:
    """AI analyst's verdict on a potential entry."""

    direction: str  # LONG / SHORT / FLAT
    ai_confidence: int  # 0-100
    reasoning: str
    model_used: str


ANALYST_PROMPT = """You are a crypto derivatives analyst. Analyze this entry signal.

Symbol: {symbol}
Signal direction: {direction}
Current price: {price}
Regime: {regime}

TA Indicators (1H candles):
RSI(14): {rsi}
Bollinger Bands: upper={bb_upper}, middle={bb_mid}, lower={bb_lower}
MACD: line={macd_line}, signal={macd_signal}, histogram={macd_hist}
ATR(14): {atr}
EMA(200): {ema}
Price vs EMA: {price_vs_ema}

Order book spread: {spread_pct:.4f}
Funding rate: {funding_rate}
Open interest change: {oi_change}

Respond with ONLY a JSON object (no markdown, no explanation):
{{"direction": "LONG" or "SHORT" or "FLAT", "confidence": 0-100, "reasoning": "one sentence"}}

Rules:
- FLAT means reject the trade (AI says no)
- Confidence 0-30 = strong reject, 31-60 = weak, 61-80 = moderate, 81-100 = strong
- Focus on: trend alignment, RSI extremes, MACD momentum, BB squeeze
- If indicators conflict, lean toward FLAT (capital preservation)
"""


class CryptoAnalyst:
    """AI pre-entry analyst. Runs in ambiguous confidence zone only."""

    def __init__(
        self,
        ai_client: AIClient,
        ohlcv_fetcher: OHLCVFetcher,
        redis_client: Any = None,
        cache_ttl: int = 300,
    ) -> None:
        self.ai_client = ai_client
        self.fetcher = ohlcv_fetcher
        self.redis = redis_client
        self.cache_ttl = cache_ttl

    async def analyze(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        regime: str,
        spread_pct: float,
        funding_rate: float,
        oi_change: float,
        price: Decimal,
        recent_trades: str = "",
    ) -> Optional[AnalystResult]:
        """Run AI analysis on an ambiguous signal. Returns None if unavailable."""
        cache_key = f"analyst:{symbol}:{int(time.time()) // self.cache_ttl}"
        if self.redis:
            cached = await self.redis.get_ai_cache(cache_key)
            if cached:
                logger.debug(f"Analyst cache hit: {symbol}")
                return AnalystResult(**cached)

        candles = await self.fetcher.fetch(symbol, "1h", 200)
        if len(candles) < 50:
            logger.warning(f"Analyst: insufficient candles for {symbol}: {len(candles)}")
            return None

        closes = [Decimal(str(c[4])) for c in candles]
        highs = [Decimal(str(c[2])) for c in candles]
        lows = [Decimal(str(c[3])) for c in candles]

        rsi, bb, macd, atr, ema = await asyncio.gather(
            asyncio.to_thread(calculate_rsi, closes, 14),
            asyncio.to_thread(calculate_bollinger_bands, closes, 20),
            asyncio.to_thread(calculate_macd, closes),
            asyncio.to_thread(calculate_atr, highs, lows, closes, 14),
            asyncio.to_thread(calculate_ema, closes, 200),
        )

        price_vs_ema = ""
        if ema and ema > 0:
            pct = (price - ema) / ema * 100
            price_vs_ema = f"{pct:+.2f}% from EMA200"

        prompt = ANALYST_PROMPT.format(
            symbol=symbol,
            direction=direction,
            price=price,
            regime=regime,
            rsi=rsi or "N/A",
            bb_upper=bb[0] if bb else "N/A",
            bb_mid=bb[1] if bb else "N/A",
            bb_lower=bb[2] if bb else "N/A",
            macd_line=macd[0] if macd else "N/A",
            macd_signal=macd[1] if macd else "N/A",
            macd_hist=macd[2] if macd else "N/A",
            atr=atr or "N/A",
            ema=ema or "N/A",
            price_vs_ema=price_vs_ema or "N/A",
            spread_pct=spread_pct,
            funding_rate=funding_rate,
            oi_change=oi_change,
        )
        if recent_trades:
            prompt = recent_trades + "\n\n" + prompt

        response = await self.ai_client.complete(prompt, max_tokens=256)
        if not response:
            metrics.ai_analyst_calls.labels(result="unavailable").inc()
            logger.warning(f"Analyst: AI unavailable for {symbol}")
            return None

        result = self._parse_response(response)
        if result is None:
            metrics.ai_analyst_calls.labels(result="parse_error").inc()
            logger.warning(f"Analyst: parse failed for {symbol}, raw={response[:200]}")
            return None

        metrics.ai_analyst_calls.labels(result="success").inc()

        if self.redis:
            await self.redis.set_ai_cache(cache_key, {
                "direction": result.direction,
                "ai_confidence": result.ai_confidence,
                "reasoning": result.reasoning,
                "model_used": result.model_used,
            }, ttl=self.cache_ttl)
        logger.info(f"Analyst: {symbol} {direction} -> {result.direction} conf={result.ai_confidence}")
        return result

    def _parse_response(self, response: str) -> Optional[AnalystResult]:
        """Parse AI JSON response into AnalystResult. Handles both JSON and free-form text."""
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            # Try JSON parse first
            try:
                data = json.loads(text)
                direction = data.get("direction", "FLAT").upper()
                confidence = int(data.get("confidence", 0))
                reasoning = data.get("reasoning", "")
            except json.JSONDecodeError:
                # Reasoning model: extract from free-form text
                import re
                direction = "FLAT"
                confidence = 50
                reasoning = text[:500]

                text_upper = text.upper()
                if re.search(r'\b(LONG|BULLISH|BUY)\b', text_upper):
                    direction = "LONG"
                elif re.search(r'\b(SHORT|BEARISH|SELL)\b', text_upper):
                    direction = "SHORT"

                conf_match = re.search(r'confidence[:\s]*(\d+)', text_upper)
                if conf_match:
                    confidence = int(conf_match.group(1))
                else:
                    if any(w in text_upper for w in ['STRONG', 'HIGH', 'VERY']):
                        confidence = 70
                    elif any(w in text_upper for w in ['WEAK', 'LOW', 'UNCERTAIN']):
                        confidence = 35
                    elif direction == "FLAT":
                        confidence = 40

                logger.info(f"Analyst: parsed from reasoning text — dir={direction} conf={confidence}")

            if direction not in ("LONG", "SHORT", "FLAT"):
                direction = "FLAT"
            confidence = max(0, min(100, confidence))

            return AnalystResult(
                direction=direction,
                ai_confidence=confidence,
                reasoning=reasoning,
                model_used=self.ai_client.model,
            )
        except (ValueError, KeyError) as e:
            logger.error(f"Analyst parse error: {e}")
            return None
