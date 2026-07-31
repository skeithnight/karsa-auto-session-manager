"""AI Exit Brain — AI-driven exit decisions for ambiguous positions.

Phase 3 (AI Role Reversal): AI is the primary exit decision-maker for positions
in the "ambiguous zone" — not at clear SL or TP, but in the messy middle where
deterministic logic struggles.

Activates when:
  - Position is +0.3R to +0.8R (profitable but not clear win)
  - OR holding > 50% of max_hold_time
  - Rate limited: max 1 AI call per position per 5 minutes

Actions: HOLD, TIGHTEN_TRAIL, PARTIAL_EXIT, FULL_EXIT
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.ai_client import AIClient

EXIT_BRAIN_PROMPT = """You are managing an open {direction} position on {symbol}.

Position State:
- Entry: {entry_price}
- Current: {current_price} ({pnl_pct:+.2f}%)
- Stop Loss: {sl_price} (exchange-side)
- Take Profit: {tp_price}
- Hold time: {hold_minutes} minutes / {max_hold_minutes} max
- R-Multiple: {r_multiple:.2f}R

Market Context:
- Regime: {regime} (was {regime_at_entry} at entry)
- CVD slope: {cvd_slope} (momentum: {momentum_desc})
- Funding: {funding_rate} (next payment in {funding_hours}h)
- Volume vs 20-bar avg: {volume_ratio:.1f}x

Choose ONE action:
1. HOLD — conviction remains, let the trade play out
2. TIGHTEN_TRAIL — move SL to {suggested_trail_sl} (lock in {trail_lock_pct}% gain)
3. PARTIAL_EXIT — close 50%, let rest trail
4. FULL_EXIT — close now at market

Respond with ONLY a JSON object:
{{"action": "HOLD|TIGHTEN_TRAIL|PARTIAL_EXIT|FULL_EXIT", "confidence": 0-100, "reasoning": "<30 words max>"}}
"""


@dataclass
class ExitDecision:
    """AI exit brain's decision for an open position."""

    action: str  # HOLD, TIGHTEN_TRAIL, PARTIAL_EXIT, FULL_EXIT
    confidence: int  # 0-100
    reasoning: str
    suggested_sl: float | None = None  # For TIGHTEN_TRAIL


class AIExitBrain:
    """AI-driven exit decisions for positions in the ambiguous zone.

    Usage:
        brain = AIExitBrain(ai_client)
        decision = await brain.evaluate_exit(position, market_data)
        if decision.action == "PARTIAL_EXIT":
            # Execute partial exit via SOR
    """

    # Ambiguous zone bounds
    R_MIN = 0.3   # Minimum R-multiple to activate
    R_MAX = 2.0   # Maximum R-multiple (above this = clear win, no AI needed)
    HOLD_PCT_MIN = 0.3  # Minimum hold time percentage to activate

    # Rate limiting
    COOLDOWN_SECS = 300  # 5 minutes between AI calls per position

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai = ai_client
        self._last_call: dict[str, float] = {}  # position_key -> timestamp

    def _position_key(self, symbol: str, direction: str) -> str:
        return f"{symbol}:{direction}"

    def _cooldown_expired(self, symbol: str, direction: str) -> bool:
        """Check if enough time has passed since last AI call for this position."""
        key = self._position_key(symbol, direction)
        last = self._last_call.get(key, 0)
        return (time.time() - last) >= self.COOLDOWN_SECS

    async def evaluate_exit(
        self,
        position: dict[str, Any],
        market_data: dict[str, Any],
    ) -> ExitDecision | None:
        """Evaluate whether to exit, hold, or tighten a position.

        Args:
            position: Dict with symbol, direction, entry_price, current_price,
                     sl_price, tp_price, hold_minutes, max_hold_minutes, regime_at_entry.
            market_data: Dict with regime, cvd_slope, funding_rate, volume_ratio,
                        atr, r_multi, etc.

        Returns:
            ExitDecision or None if AI not needed (clear win/loss) or rate limited.
        """
        symbol = position["symbol"]
        direction = position["direction"]

        # Calculate R-multiple
        entry = float(position.get("entry_price", 0))
        current = float(position.get("current_price", 0))
        sl = float(position.get("sl_price", 0))

        if entry <= 0 or sl <= 0:
            return None

        risk = abs(entry - sl)
        if risk <= 0:
            return None

        if direction == "LONG":
            r_multi = (current - entry) / risk
        else:
            r_multi = (entry - current) / risk

        # Check if in ambiguous zone
        hold_pct = position.get("hold_minutes", 0) / max(position.get("max_hold_minutes", 1440), 1)

        if r_multi < self.R_MIN or r_multi > self.R_MAX:
            return None  # Clear loss or clear win — no AI needed
        if hold_pct < self.HOLD_PCT_MIN:
            return None  # Too early — let position develop

        # Rate limit
        if not self._cooldown_expired(symbol, direction):
            return None

        # AI not available
        if self._ai is None:
            return None

        # Build prompt
        pnl_pct = ((current - entry) / entry * 100) if direction == "LONG" else ((entry - current) / entry * 100)

        prompt = EXIT_BRAIN_PROMPT.format(
            symbol=symbol,
            direction=direction,
            entry_price=f"{entry:.4f}",
            current_price=f"{current:.4f}",
            pnl_pct=pnl_pct,
            sl_price=f"{sl:.4f}",
            tp_price=f"{position.get('tp_price', 'TRAILING')}",
            hold_minutes=position.get("hold_minutes", 0),
            max_hold_minutes=position.get("max_hold_minutes", 1440),
            r_multiple=r_multi,
            regime=market_data.get("regime", "UNKNOWN"),
            regime_at_entry=position.get("regime_at_entry", "UNKNOWN"),
            cvd_slope=market_data.get("cvd_slope", 0),
            momentum_desc=self._momentum_desc(market_data.get("cvd_slope", 0)),
            funding_rate=market_data.get("funding_rate", 0),
            funding_hours=market_data.get("funding_hours", 4),
            volume_ratio=market_data.get("volume_ratio", 1.0),
            suggested_trail_sl=f"{self._suggested_trail_sl(entry, current, direction, market_data.get('atr', 0)):.4f}",
            trail_lock_pct=max(0, pnl_pct * 0.5),
        )

        # Call AI with timeout
        try:
            response = await asyncio.wait_for(
                self._ai.ask(prompt, max_tokens=200),
                timeout=8.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("AI exit brain call failed: %s", e)
            return None

        # Record cooldown
        self._last_call[self._position_key(symbol, direction)] = time.time()

        # Parse response
        return self._parse_exit(response)

    def _suggested_trail_sl(
        self, entry: float, current: float, direction: str, atr: float
    ) -> float:
        """Calculate suggested trailing SL for TIGHTEN_TRAIL action."""
        if direction == "LONG":
            # Move SL to entry + 50% of unrealized profit
            profit = current - entry
            return entry + profit * 0.5
        else:
            profit = entry - current
            return entry - profit * 0.5

    def _momentum_desc(self, cvd_slope: float) -> str:
        """Describe CVD slope in words."""
        if cvd_slope > 0.1:
            return "strong buying"
        elif cvd_slope > 0:
            return "mild buying"
        elif cvd_slope < -0.1:
            return "strong selling"
        elif cvd_slope < 0:
            return "mild selling"
        return "neutral"

    def _parse_exit(self, response: str) -> ExitDecision | None:
        """Parse AI response into ExitDecision."""
        try:
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text)
            action = data.get("action", "HOLD")
            if action not in ("HOLD", "TIGHTEN_TRAIL", "PARTIAL_EXIT", "FULL_EXIT"):
                action = "HOLD"

            return ExitDecision(
                action=action,
                confidence=min(100, max(0, data.get("confidence", 50))),
                reasoning=data.get("reasoning", ""),
                suggested_sl=data.get("suggested_sl"),
            )

        except Exception as e:
            logger.debug("Failed to parse AI exit response: %s", e)
            return None
