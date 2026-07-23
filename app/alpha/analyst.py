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
from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loguru import logger

from app.ai.circuit_breaker import AICircuitBreaker
from app.alpha.ta_tools import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)
from app.core import metrics
from app.core.ai_client import AIClient
from app.data.ohlcv_fetcher import OHLCVFetcher


@dataclass
class AnalystResult:
    """AI analyst's verdict on a potential entry."""

    direction: str  # LONG / SHORT / FLAT
    ai_confidence: int  # 0-100
    reasoning: str
    model_used: str
    decision_recommendation: str = "NO_TRADE"
    primary_edge: str = ""
    critical_risk_flag: str = ""
    suggested_position_multiplier: float = 1.0


ANALYST_PROMPT = """You are a crypto derivatives analyst. Analyze this entry signal.

Symbol: {symbol}
Signal direction: {direction}
Current price: {price}
Regime: {regime}
Session Context: {session_context}

TA Indicators (1H candles):
RSI(14): {rsi}
Bollinger Bands: upper={bb_upper}, middle={bb_mid}, lower={bb_lower}
MACD: line={macd_line}, signal={macd_signal}, histogram={macd_hist}
ATR(14): {atr}
EMA(200): {ema}
Price vs EMA: {price_vs_ema}

Order book spread: {spread_pct:.4f}
Funding rate: {funding_rate}
Funding Rate Divergence: {funding_divergence}
Open interest change: {oi_change}
Liquidation Proximity: {liquidation_proximity}
FLOW: USDT_In: ${usdt_inflow_m}M | BTC_Out: {btc_outflow_count} BTC | Liq_Vol: ${liq_volume_m}M ({liq_dominant_side})

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "confidence_score": 0-100,
  "decision_recommendation": "STRONG_BUY" | "WEAK_BUY" | "NO_TRADE" | "STRONG_SHORT" | "WEAK_SHORT",
  "primary_edge": "<10-word summary>",
  "critical_risk_flag": "<10-word summary>",
  "suggested_position_multiplier": <float 0.5 to 1.5>
}}

Rules:
- NO_TRADE means reject the trade (AI says no)
- Confidence 0-30 = strong reject, 31-60 = weak, 61-80 = moderate, 81-100 = strong
- Focus on: trend alignment, RSI extremes, MACD momentum, BB squeeze, Funding/OI anomalies
- If indicators conflict, lean toward NO_TRADE (capital preservation)
"""

SNIPER_PRE_APPROVAL_PROMPT = """You are an elite crypto market-maker and sniper. 
Analyze the current orderbook depth, funding rates, and open interest for potential liquidation cascades.

Symbol: {symbol}
Current price: {price}
Orderbook Imbalance: {ob_imbalance}
Funding Rate: {funding_rate}
Open Interest Change: {oi_change}

If a high-probability fragile zone exists, you must pre-approve a limit order trap.
Provide a target_entry_price at a structural support/resistance level.
Define strict invalidation conditions (e.g. price breach).

Respond with ONLY a JSON object (no markdown, no explanation):
{{"target_entry_price": 60000.0, "confidence": 0-100, "thesis": "one sentence", "invalidation_conditions": {{"price_breach": 62000.0}}}}
If no setup exists, return confidence 0 and target_entry_price 0.
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
        self.circuit_breaker = AICircuitBreaker(failure_threshold=3, reset_timeout_seconds=300)

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
        flow_data: dict[str, Any] | None = None,
    ) -> AnalystResult | None:
        """Run AI analysis on an ambiguous signal. Returns None if unavailable."""
        if not self.circuit_breaker.allow_request():
            metrics.ai_rejection_total.inc()
            return AnalystResult(direction="FLAT", ai_confidence=0, reasoning="AI_CIRCUIT_OPEN", model_used="circuit_breaker")

        cache_key = f"analyst:{symbol}:{int(time.time()) // self.cache_ttl}"
        if self.redis:
            try:
                raw = await self.redis.get(f"ai:cache:{cache_key}")
                if raw:
                    cached = json.loads(raw)
                    logger.debug(f"Analyst cache hit: {symbol}")
                    return AnalystResult(**cached)
            except Exception:
                logger.debug(f"Analyst cache read failed for {symbol}")

        candles = await self.fetcher.fetch(symbol, "1h", 200)
        if len(candles) < 50:
            logger.warning(
                f"Analyst: insufficient candles for {symbol}: {len(candles)}"
            )
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

        # Context Enrichment: Session Context
        utc_hour = datetime.utcnow().hour
        if 0 <= utc_hour < 8:
            session_context = "Asia Session"
        elif 8 <= utc_hour < 16:
            session_context = "London Session"
        else:
            session_context = "NY Session"

        # Context Enrichment: Liquidation Proximity Heuristic
        # Modulo 1000 for major rounds, modulo 500 for mid rounds
        price_f = float(price)
        dist_to_1000 = min(price_f % 1000, 1000 - (price_f % 1000))
        dist_to_500 = min(price_f % 500, 500 - (price_f % 500))
        liquidation_proximity = f"{(dist_to_1000 / price_f * 100):.2f}% from major round number, {(dist_to_500 / price_f * 100):.2f}% from mid round number"

        # Context Enrichment: Funding Rate Divergence
        funding_divergence = "Neutral"
        if direction == "LONG" and funding_rate > 0.0005:
            funding_divergence = "Opposing (Longing into high positive funding)"
        elif direction == "SHORT" and funding_rate < -0.0005:
            funding_divergence = "Opposing (Shorting into high negative funding)"
        elif direction == "LONG" and funding_rate < 0:
            funding_divergence = "Favorable (Getting paid to Long)"
        elif direction == "SHORT" and funding_rate > 0:
            funding_divergence = "Favorable (Getting paid to Short)"

        # Alternative Data Flow Metrics
        fd = flow_data or {}
        usdt_inflow_m = float(fd.get("usdt_inflow_m", 0.0))
        btc_outflow_count = float(fd.get("btc_outflow_count", 0.0))
        liq_volume_m = float(fd.get("liq_volume_m", 0.0))
        liq_dominant_side = str(fd.get("liq_dominant_side", "Neutral"))

        prompt = ANALYST_PROMPT.format(
            symbol=symbol,
            direction=direction,
            price=price,
            regime=regime,
            session_context=session_context,
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
            funding_divergence=funding_divergence,
            oi_change=oi_change,
            liquidation_proximity=liquidation_proximity,
            usdt_inflow_m=usdt_inflow_m,
            btc_outflow_count=btc_outflow_count,
            liq_volume_m=liq_volume_m,
            liq_dominant_side=liq_dominant_side,
        )
        if recent_trades:
            prompt = recent_trades + "\n\n" + prompt

        try:
            metrics.ai_request_total.inc()
            response = await asyncio.wait_for(
                self.ai_client.complete(prompt, max_tokens=1024),
                timeout=30.0
            )
            self.circuit_breaker.record_success()
        except TimeoutError:
            self.circuit_breaker.record_failure()
            metrics.ai_timeout_total.inc()
            logger.warning(f"Analyst: AI request timed out for {symbol}")
            return AnalystResult(direction="FLAT", ai_confidence=0, reasoning="AI_TIMEOUT", model_used="circuit_breaker")
        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.warning(f"Analyst: AI request failed for {symbol}: {e}")
            return AnalystResult(direction="FLAT", ai_confidence=0, reasoning="AI_REQUEST_FAILED", model_used="circuit_breaker")

        if not response:
            metrics.ai_analyst_calls.labels(result="unavailable").inc()
            logger.warning(f"Analyst: AI unavailable for {symbol}")
            return AnalystResult(direction="FLAT", ai_confidence=0, reasoning="AI_REQUEST_FAILED", model_used="circuit_breaker")

        result = self._parse_response(response)
        if result is None:
            metrics.ai_analyst_calls.labels(result="parse_error").inc()
            logger.warning(f"Analyst: parse failed for {symbol}, raw={response[:200]}")
            return None

        # Short Squeeze Confluence Boost (+20% AI confidence)
        if direction in ("LONG", "buy") and usdt_inflow_m >= 10.0 and liq_volume_m >= 20.0:
            boosted = min(100, result.ai_confidence + 20)
            logger.info(
                f"Analyst Short Squeeze Confluence for {symbol}: AI confidence boosted {result.ai_confidence} -> {boosted} "
                f"(USDT Inflow=${usdt_inflow_m}M, Liq=${liq_volume_m}M)"
            )
            result.ai_confidence = boosted
            result.reasoning += f" | Boost: +20% short squeeze setup (USDT inflow ${usdt_inflow_m}M + Liq ${liq_volume_m}M)"

        metrics.ai_analyst_calls.labels(result="success").inc()
        metrics.ai_confidence.labels(symbol=symbol).set(result.ai_confidence)

        if self.redis:
            try:
                await self.redis.set(
                    f"ai:cache:{cache_key}",
                    json.dumps(
                        {
                            "direction": result.direction,
                            "ai_confidence": result.ai_confidence,
                            "reasoning": result.reasoning,
                            "model_used": result.model_used,
                            "decision_recommendation": result.decision_recommendation,
                            "primary_edge": result.primary_edge,
                            "critical_risk_flag": result.critical_risk_flag,
                            "suggested_position_multiplier": result.suggested_position_multiplier,
                        }
                    ),
                    ex=self.cache_ttl,
                )
            except Exception:
                logger.debug(f"Analyst cache write failed for {symbol}")
        reasoning_preview = (result.reasoning or "")[:150].replace("\n", " ")
        logger.info(
            f"Analyst: {symbol} {direction} -> {result.direction} conf={result.ai_confidence} | {reasoning_preview}"
        )
        return result

    def _parse_response(self, response: str) -> AnalystResult | None:
        """Parse AI JSON response into AnalystResult. Handles both JSON and free-form text."""
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            decision_recommendation = "NO_TRADE"
            confidence = 0
            primary_edge = ""
            critical_risk_flag = ""
            multiplier = 1.0
            
            try:
                data = json.loads(text)
                decision_recommendation = data.get("decision_recommendation", "NO_TRADE").upper()
                confidence = int(data.get("confidence_score", data.get("confidence", 0)))
                primary_edge = data.get("primary_edge", "")
                critical_risk_flag = data.get("critical_risk_flag", "")
                multiplier = float(data.get("suggested_position_multiplier", 1.0))
            except json.JSONDecodeError:
                # Fallback extraction from free-form text
                import re

                text_upper = text.upper()
                if "STRONG_BUY" in text_upper or "WEAK_BUY" in text_upper:
                    decision_recommendation = "STRONG_BUY" if "STRONG_BUY" in text_upper else "WEAK_BUY"
                elif "STRONG_SHORT" in text_upper or "WEAK_SHORT" in text_upper:
                    decision_recommendation = "STRONG_SHORT" if "STRONG_SHORT" in text_upper else "WEAK_SHORT"
                else:
                    decision_recommendation = "NO_TRADE"

                conf_match = re.search(r"confidence[:\s]*(\d+)", text_upper)
                if conf_match:
                    confidence = int(conf_match.group(1))
                else:
                    confidence = 50 if decision_recommendation != "NO_TRADE" else 0

                primary_edge = "Extracted from freeform text"
                critical_risk_flag = "JSON parse failed"
                multiplier = 1.0

            # Map decision to legacy direction
            if "BUY" in decision_recommendation:
                direction = "LONG"
            elif "SHORT" in decision_recommendation:
                direction = "SHORT"
            else:
                direction = "FLAT"
                
            confidence = max(0, min(100, confidence))
            multiplier = max(0.5, min(1.5, multiplier))

            return AnalystResult(
                direction=direction,
                ai_confidence=confidence,
                reasoning=f"Edge: {primary_edge} | Risk: {critical_risk_flag}",
                model_used=self.ai_client.model,
                decision_recommendation=decision_recommendation,
                primary_edge=primary_edge,
                critical_risk_flag=critical_risk_flag,
                suggested_position_multiplier=multiplier,
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Analyst parse error: {e}")
            return None

    async def pre_approve_sniper(
        self,
        symbol: str,
        price: float,
        ob_imbalance: float,
        funding_rate: float,
        oi_change: float,
    ) -> dict[str, Any] | None:
        """Ask AI for Sniper Trap pre-approval."""
        if not self.circuit_breaker.allow_request():
            return None
            
        prompt = SNIPER_PRE_APPROVAL_PROMPT.format(
            symbol=symbol,
            price=price,
            ob_imbalance=ob_imbalance,
            funding_rate=funding_rate,
            oi_change=oi_change,
        )
        
        try:
            response = await asyncio.wait_for(
                self.ai_client.complete(prompt, max_tokens=1024),
                timeout=3.0
            )
            self.circuit_breaker.record_success()
        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.warning(f"Analyst: Sniper AI request failed for {symbol}: {e}")
            return None
            
        if not response:
            return None
            
        try:
            # simple json extraction
            text = response.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except Exception as e:
            logger.warning(f"Analyst: Sniper parse failed for {symbol}: {e}")
            return None
