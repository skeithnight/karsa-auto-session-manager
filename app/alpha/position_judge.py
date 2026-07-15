"""Position Judge — AI post-entry position assessment.

Runs in CheckpointManager when position is in ambiguous zone.
2-tier: cheap pass (haiku, fast) -> escalate if ambiguous.
3 consecutive HOLDs on a losing position -> forced EXIT.
"""

from __future__ import annotations

import json
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
class JudgeVerdict:
    """AI judge's verdict on an open position."""

    action: str  # HOLD / EXIT / TIGHTEN_STOP
    confidence: int  # 0-100
    reasoning: str
    tier_used: str  # cheap / escalated


CHEAP_PROMPT = """You are a crypto position manager. Evaluate this open position quickly.

Symbol: {symbol}
Side: {side}
Entry: {entry_price}
Current: {current_price}
PnL: {pnl_pct:.2f}%
Held: {elapsed_hours:.1f}h
Regime: {regime}
ATR: {atr}

Decide: HOLD, EXIT, or TIGHTEN_STOP.
- EXIT if: trend reversed, momentum dead, risk/reward bad
- TIGHTEN_STOP if: in profit but weakening
- HOLD if: thesis intact

Respond with ONLY JSON: {{"action": "HOLD"|"EXIT"|"TIGHTEN_STOP", "confidence": 0-100, "reasoning": "one sentence"}}
"""

ESCALATED_PROMPT = """You are a senior crypto derivatives trader. Deep evaluation of this position.

Symbol: {symbol}
Side: {side}
Entry: {entry_price}
Current: {current_price}
Peak: {peak_price}
PnL: {pnl_pct:.2f}%
Held: {elapsed_hours:.1f}h
Regime: {regime}

Technical analysis (1H):
RSI(14): {rsi}
Bollinger: upper={bb_upper}, mid={bb_mid}, lower={bb_lower}
MACD: line={macd_line}, signal={macd_signal}, hist={macd_hist}
ATR(14): {atr}
EMA(200): {ema}
Price vs EMA: {price_vs_ema}

Previous AI verdict: {prev_action} (conf={prev_conf})
Consecutive holds on losing position: {hold_count}

Rules:
- If trend reversed against position: EXIT
- If RSI extreme against position (>75 for short, <25 for long): EXIT
- If MACD histogram declining 3+ bars: EXIT
- If 3+ consecutive holds on a loser: EXIT (stop grinding)
- If in profit >2x ATR and momentum fading: TIGHTEN_STOP
- Otherwise: HOLD

Respond with ONLY JSON: {{"action": "HOLD"|"EXIT"|"TIGHTEN_STOP", "confidence": 0-100, "reasoning": "one sentence"}}
"""


class PositionJudge:
    """AI post-entry position judge with 2-tier escalation."""

    def __init__(
        self,
        ai_client: AIClient,
        ohlcv_fetcher: OHLCVFetcher,
        redis_client: Any = None,
        cheap_timeout: float = 5.0,
        escalated_timeout: float = 15.0,
    ) -> None:
        self.ai_client = ai_client
        self.fetcher = ohlcv_fetcher
        self.redis = redis_client
        self.cheap_timeout = cheap_timeout
        self.escalated_timeout = escalated_timeout
        self._hold_counters: dict[str, int] = {}

    async def judge(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        current_price: Decimal,
        peak_price: Decimal,
        atr: Decimal,
        regime: str,
        elapsed_seconds: float,
        prev_action: str = "NONE",
        prev_conf: int = 0,
        recent_trades: str = "",
        is_checkpoint_review: bool = False,
    ) -> Optional[JudgeVerdict]:
        """Judge an open position. Returns None if AI unavailable."""
        pnl_pct = float((current_price - entry_price) / entry_price * 100)
        if side == "sell":
            pnl_pct = -pnl_pct

        hold_key = f"{symbol}:{side}"
        hold_count = self._hold_counters.get(hold_key, 0)

        max_holds = 2 if is_checkpoint_review else 3
        # Forced exit after consecutive HOLDs on loser
        if hold_count >= max_holds and pnl_pct < 0:
            metrics.ai_consecutive_hold_exits.inc()
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict="EXIT", tier="forced").inc()
            logger.warning(f"PositionJudge: forced EXIT {symbol} {side} after {hold_count} HOLDs, pnl={pnl_pct:.2f}%")
            self._hold_counters[hold_key] = 0
            return JudgeVerdict(
                action="EXIT",
                confidence=90,
                reasoning=f"Forced exit after {hold_count} consecutive HOLDs on losing position",
                tier_used="forced",
            )

        # Cheap pass
        verdict = await self._cheap_pass(
            symbol, side, entry_price, current_price, atr, regime,
            elapsed_seconds,
        )

        if verdict and verdict.action != "HOLD":
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict=verdict.action, tier="cheap").inc()
            self._hold_counters[hold_key] = 0
            return verdict

        # Escalated pass if ambiguous (HOLD with low confidence)
        if verdict and verdict.confidence >= 70:
            if pnl_pct < 0:
                self._hold_counters[hold_key] = hold_count + 1
            return verdict

        escalated = await self._escalated_pass(
            symbol, side, entry_price, current_price, peak_price, atr, regime,
            elapsed_seconds, prev_action, prev_conf, hold_count, recent_trades,
        )

        if escalated:
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict=escalated.action, tier="escalated").inc()
            if escalated.action == "HOLD" and pnl_pct < 0:
                self._hold_counters[hold_key] = hold_count + 1
            else:
                self._hold_counters[hold_key] = 0
            return escalated

        # Both tiers failed — conservative HOLD (fail-safe: never exit without AI)
        metrics.ai_judge_verdict.labels(symbol=symbol, verdict="HOLD", tier="fallback").inc()
        if pnl_pct < 0:
            self._hold_counters[hold_key] = hold_count + 1
        return JudgeVerdict(
            action="HOLD",
            confidence=30,
            reasoning="AI unavailable, conservative hold",
            tier_used="fallback",
        )

    async def _cheap_pass(
        self, symbol, side, entry_price, current_price, atr, regime, elapsed_seconds,
    ) -> Optional[JudgeVerdict]:
        """Quick haiku pass, no TA tools."""
        pnl_pct = float((current_price - entry_price) / entry_price * 100)
        if side == "sell":
            pnl_pct = -pnl_pct

        prompt = CHEAP_PROMPT.format(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=current_price,
            pnl_pct=pnl_pct,
            elapsed_hours=elapsed_seconds / 3600,
            regime=regime,
            atr=atr,
        )

        response = await self.ai_client.complete(prompt, max_tokens=128)
        if not response:
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict="unavailable", tier="cheap").inc()
            return None

        verdict = self._parse_response(response, "cheap")
        if verdict:
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict=verdict.action, tier="cheap").inc()
        return verdict

    async def _escalated_pass(
        self, symbol, side, entry_price, current_price, peak_price, atr, regime,
        elapsed_seconds, prev_action, prev_conf, hold_count, recent_trades="",
    ) -> Optional[JudgeVerdict]:
        """Full TA pass with escalated model."""
        candles = await self.fetcher.fetch(symbol, "1h", 200)
        rsi = bb = macd = ema = price_vs_ema = "N/A"
        atr_str = str(atr)

        if len(candles) >= 50:
            closes = [Decimal(str(c[4])) for c in candles]
            highs = [Decimal(str(c[2])) for c in candles]
            lows = [Decimal(str(c[3])) for c in candles]

            rsi_val = calculate_rsi(closes, 14)
            bb_val = calculate_bollinger_bands(closes, 20)
            macd_val = calculate_macd(closes)
            atr_val = calculate_atr(highs, lows, closes, 14)
            ema_val = calculate_ema(closes, 200)

            rsi = str(rsi_val) if rsi_val else "N/A"
            if bb_val:
                bb = f"upper={bb_val[0]}, mid={bb_val[1]}, lower={bb_val[2]}"
            if macd_val:
                macd = f"line={macd_val[0]}, signal={macd_val[1]}, hist={macd_val[2]}"
            if atr_val:
                atr_str = str(atr_val)
            ema = str(ema_val) if ema_val else "N/A"

            if ema_val and ema_val > 0:
                pct = (current_price - ema_val) / ema_val * 100
                price_vs_ema = f"{pct:+.2f}%"

        pnl_pct = float((current_price - entry_price) / entry_price * 100)
        if side == "sell":
            pnl_pct = -pnl_pct

        def _bb(part: str) -> str:
            if f"{part}=" in bb:
                return bb.split(f"{part}=")[1].split(",")[0] if "," in bb.split(f"{part}=")[1] else bb.split(f"{part}=")[1]
            return "N/A"

        def _macd(part: str) -> str:
            if f"{part}=" in macd:
                rest = macd.split(f"{part}=")[1]
                return rest.split(",")[0] if "," in rest else rest
            return "N/A"

        prompt = ESCALATED_PROMPT.format(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=current_price,
            peak_price=peak_price,
            pnl_pct=pnl_pct,
            elapsed_hours=elapsed_seconds / 3600,
            regime=regime,
            rsi=rsi,
            bb_upper=_bb("upper"),
            bb_mid=_bb("mid"),
            bb_lower=_bb("lower"),
            macd_line=_macd("line"),
            macd_signal=_macd("signal"),
            macd_hist=_macd("hist"),
            atr=atr_str,
            ema=ema,
            price_vs_ema=price_vs_ema,
            prev_action=prev_action,
            prev_conf=prev_conf,
            hold_count=hold_count,
        )
        if recent_trades:
            prompt = recent_trades + "\n\n" + prompt

        response = await self.ai_client.complete(prompt, max_tokens=256)
        if not response:
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict="unavailable", tier="escalated").inc()
            return None

        verdict = self._parse_response(response, "escalated")
        if verdict:
            metrics.ai_judge_verdict.labels(symbol=symbol, verdict=verdict.action, tier="escalated").inc()
        return verdict

    def _parse_response(self, response: str, tier: str) -> Optional[JudgeVerdict]:
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
            action = data.get("action", "HOLD").upper()
            confidence = int(data.get("confidence", 50))
            reasoning = data.get("reasoning", "")

            if action not in ("HOLD", "EXIT", "TIGHTEN_STOP"):
                action = "HOLD"
            confidence = max(0, min(100, confidence))

            return JudgeVerdict(
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                tier_used=tier,
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"PositionJudge parse error ({tier}): {e}")
            return None

    def reset_hold_counter(self, symbol: str, side: str) -> None:
        key = f"{symbol}:{side}"
        self._hold_counters.pop(key, None)
