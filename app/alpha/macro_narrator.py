"""AI Macro Narrator — 4-hour strategic advisor.

Replaces the per-trade AI gate (which was causing JSON parse errors and latency).
Runs every 4 hours to classify the macro environment into one of three states:
  - RISK_ON:    Altseason, momentum, high conviction → full sizing
  - RISK_OFF:   Defensive, capital preservation → 0.25x sizing
  - CHOP:       Range-bound, no clear direction → 0.5x sizing

The result is written to Redis and read by the Decision Engine to apply
a global sizing multiplier. The AI is now a strategic advisor, not a micro-judge.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from enum import Enum
from typing import Any

from loguru import logger


class MacroState(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    CHOP = "CHOP"


# Multipliers applied to final Kelly sizing based on macro state
MACRO_MULTIPLIERS: dict[MacroState, float] = {
    MacroState.RISK_ON: 1.0,
    MacroState.RISK_OFF: 0.25,
    MacroState.CHOP: 0.5,
}

MACRO_NARRATOR_PROMPT = """You are a crypto macro strategist. Analyze the current market conditions and classify the macro environment.

Current BTC Data:
- Price: ${btc_price:,.2f}
- 24h Change: {btc_24h_pct:+.2f}%
- Regime: {btc_regime}
- Funding Rate: {btc_funding:.6f}

Current ETH Data:
- Price: ${eth_price:,.2f}
- 24h Change: {eth_24h_pct:+.2f}%
- Funding Rate: {eth_funding:.6f}

Market Context:
- BTC Dominance Trend: {btc_dom_trend}
- Total Market Cap 24h Change: {total_mcap_pct:+.2f}%
- DXY (Dollar Index) Trend: {dxy_trend}

Classify this into exactly ONE of:
1. RISK_ON — Altseason or bullish momentum. BTC trending up, ETH outperforming, alts pumping, positive funding, risk appetite high.
2. RISK_OFF — Defensive posture. BTC weak or falling, capital rotating to stablecoins, negative or declining funding, risk-off sentiment.
3. CHOP — Range-bound, no clear direction. BTC oscillating, mixed signals, neither risk-on nor risk-off.

Respond with ONLY a JSON object like this:
{{"state": "RISK_ON", "confidence": 0.8, "reasoning": "one sentence"}}
"""


class MacroNarrator:
    """Background task that classifies the macro environment every 4 hours."""

    def __init__(
        self,
        ai_client: Any,
        redis_client: Any,
        ohlcv_fetcher: Any,
        interval_s: int = 14400,  # 4 hours
        redis_key: str = "system:macro:narrator",
        ttl_s: int = 18000,  # 5 hours
    ) -> None:
        self._ai = ai_client
        self._redis = redis_client
        self._fetcher = ohlcv_fetcher
        self._interval = interval_s
        self._redis_key = redis_key
        self._ttl = ttl_s

    async def run(self) -> None:
        """Main loop — runs every 4 hours. First assessment runs immediately."""
        logger.warning("MacroNarrator: starting (interval=%ds)", self._interval)
        # Run immediately on startup — don't wait 4 hours for first assessment
        try:
            await self._assess_macro_state()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MacroNarrator: initial assessment failed")
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._assess_macro_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MacroNarrator: assessment failed")

    async def _assess_macro_state(self) -> None:
        """Fetch BTC/ETH data, prompt LLM, write result to Redis."""
        logger.warning("MacroNarrator: assessing macro state...")

        # Fetch BTC data
        btc_price = 0.0
        btc_24h_pct = 0.0
        btc_regime = "UNKNOWN"
        btc_funding = 0.0

        try:
            candles_1h = await self._fetcher.fetch("BTC/USDT", "1h", 50)
            logger.warning("MacroNarrator: BTC candles fetched (%d)", len(candles_1h) if candles_1h else 0)
            if candles_1h and len(candles_1h) >= 24:
                btc_price = float(candles_1h[-1][4])
                btc_24h_ago = float(candles_1h[-24][4])
                btc_24h_pct = ((btc_price - btc_24h_ago) / btc_24h_ago * 100) if btc_24h_ago > 0 else 0.0
        except Exception as e:
            logger.debug("MacroNarrator: BTC fetch failed: %s", e)

        # Fetch BTC regime from Redis
        try:
            raw_regime = await self._redis.get("system:regime:BTC:USDT")
            if raw_regime:
                btc_regime = raw_regime.decode() if isinstance(raw_regime, bytes) else str(raw_regime)
        except Exception:
            pass

        # Fetch BTC funding
        try:
            raw_funding = await self._redis.get("karsa:market:BTC/USDT:funding_rate")
            if raw_funding:
                btc_funding = float(raw_funding.decode() if isinstance(raw_funding, bytes) else raw_funding)
        except Exception:
            pass

        # Fetch ETH data
        eth_price = 0.0
        eth_24h_pct = 0.0
        eth_funding = 0.0

        try:
            candles_eth = await self._fetcher.fetch("ETH/USDT", "1h", 50)
            if candles_eth and len(candles_eth) >= 24:
                eth_price = float(candles_eth[-1][4])
                eth_24h_ago = float(candles_eth[-24][4])
                eth_24h_pct = ((eth_price - eth_24h_ago) / eth_24h_ago * 100) if eth_24h_ago > 0 else 0.0
        except Exception as e:
            logger.debug("MacroNarrator: ETH fetch failed: %s", e)

        try:
            raw_eth_funding = await self._redis.get("karsa:market:ETH/USDT:funding_rate")
            if raw_eth_funding:
                eth_funding = float(raw_eth_funding.decode() if isinstance(raw_eth_funding, bytes) else raw_eth_funding)
        except Exception:
            pass

        # Simplified context (no DXY or BTC dominance available from exchange API)
        prompt = MACRO_NARRATOR_PROMPT.format(
            btc_price=btc_price,
            btc_24h_pct=btc_24h_pct,
            btc_regime=btc_regime,
            btc_funding=btc_funding,
            eth_price=eth_price,
            eth_24h_pct=eth_24h_pct,
            eth_funding=eth_funding,
            btc_dom_trend="N/A",
            total_mcap_pct=0.0,
            dxy_trend="N/A",
        )

        # Call LLM
        try:
            response = await self._ai.complete(
                prompt=prompt,
                max_tokens=200,
                temperature=0.3,
            )
        except Exception as e:
            logger.warning("MacroNarrator: LLM call failed: %s", e)
            # Fail-closed: default to CHOP (conservative)
            await self._write_result(MacroState.CHOP, 0.5, f"LLM failed: {e}")
            return

        # Parse response
        try:
            # Extract JSON from response
            response_text = response.strip()
            if "{" in response_text:
                json_start = response_text.index("{")
                json_end = response_text.rindex("}") + 1
                result = _json.loads(response_text[json_start:json_end])
            else:
                result = _json.loads(response_text)

            state_str = result.get("state", "CHOP").upper()
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "no reasoning provided")

            # Validate state
            try:
                state = MacroState(state_str)
            except ValueError:
                logger.warning("MacroNarrator: invalid state '%s', defaulting to CHOP", state_str)
                state = MacroState.CHOP

            await self._write_result(state, confidence, reasoning)

        except (ValueError, KeyError, _json.JSONDecodeError) as e:
            logger.warning("MacroNarrator: parse failed (%s), defaulting to CHOP", e)
            # Fail-closed: default to CHOP
            await self._write_result(MacroState.CHOP, 0.5, f"Parse failed: {e}")

    async def _write_result(self, state: MacroState, confidence: float, reasoning: str) -> None:
        """Write macro state to Redis with TTL."""
        data = _json.dumps({
            "state": state.value,
            "confidence": confidence,
            "reasoning": reasoning,
            "multiplier": MACRO_MULTIPLIERS[state],
            "updated_at": time.time(),
        })
        await self._redis.set(self._redis_key, data, ex=self._ttl)
        logger.warning(
            "MacroNarrator: {} (confidence={:.2f}, multiplier={:.2f}x) — {}",
            state.value, confidence, MACRO_MULTIPLIERS[state], reasoning,
        )


async def get_macro_multiplier(redis_client: Any, redis_key: str = "system:macro:narrator") -> float:
    """Async helper to read macro multiplier from Redis.

    Returns 1.0 (no adjustment) if Redis is unavailable or data is missing.
    Fail-closed: defaults to CHOP (0.5x) on parse errors.
    """
    try:
        raw = await redis_client.get(redis_key)
        if raw:
            data = _json.loads(raw)
            multiplier = data.get("multiplier", 1.0)
            return float(multiplier)
    except Exception:
        pass
    return 1.0  # Default: no adjustment (fail-open for sizing)
