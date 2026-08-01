"""AI Signal Ranker — ranks multiple candidates instead of vetoing individuals.

Phase 3 (AI Role Reversal): AI no longer says "don't trade." It ranks the top
N signals by quality and adjusts sizing. Every signal gets traded — sizing reflects
conviction. Fallback to EV ordering if AI fails.

Architecture:
    AISignalRanker.rank() → list[RankedSignal]
    Batch mode: processes top 5 signals per cycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.core.ai_client import AIClient

RANKER_PROMPT = """You are an elite crypto portfolio manager. You have {n_signals} trade candidates that passed all quality checks. Your job is to RANK them from best to worst opportunity.

Portfolio State:
- Open positions: {open_positions}
- Daily P&L: {daily_pnl}
- Available capital: {available_capital}
- Session: {session_context}
- Macro regime: {macro_regime}

Candidates:
{signal_list}

For each candidate, provide:
1. rank (1 = best)
2. sizing_multiplier (0.5 to 1.5 — how much of the default size to use)
3. edge_thesis (one sentence — WHY this is the best/worst opportunity)

Respond with ONLY a JSON array (no markdown):
[{{"symbol": "BTC/USDT", "rank": 1, "sizing_multiplier": 1.2, "edge_thesis": "Funding negative, breakout from 4H range, volume confirming"}}]

Rules:
- You MUST rank ALL candidates. Do not reject any.
- sizing_multiplier < 0.7 means "weak but still tradeable"
- sizing_multiplier > 1.2 means "high conviction, increase size"
- Consider correlation: don't rank 3 correlated altcoins all as top picks
"""


@dataclass
class RankedSignal:
    """A ranked trade signal with AI-assessed sizing."""

    symbol: str
    direction: str
    ev_score: float
    rank: int
    sizing_multiplier: float  # 0.5 to 1.5
    edge_thesis: str
    original_score: float = 0.0


class AISignalRanker:
    """Ranks multiple trade candidates instead of vetoing individuals.

    Usage:
        ranker = AISignalRanker(ai_client)
        ranked = await ranker.rank(signals, portfolio_state)
        # Returns same signals, ordered by AI rank with sizing adjustments
    """

    BATCH_SIZE = 5  # Rank top 5 signals per cycle

    # Cost per 1K tokens (USD) — adjust per model
    COST_PER_1K_INPUT = 0.00015  # ~$0.15/1M input tokens
    COST_PER_1K_OUTPUT = 0.0006  # ~$0.60/1M output tokens

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai = ai_client
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self._call_count = 0

    async def rank(
        self,
        signals: list[dict[str, Any]],
        portfolio_state: dict[str, Any] | None = None,
    ) -> list[RankedSignal]:
        """Rank signals by AI-assessed quality. Never rejects.

        Args:
            signals: List of signal dicts with keys: symbol, direction, score, ev_score, regime, etc.
            portfolio_state: Optional dict with open_positions, daily_pnl, available_capital.

        Returns:
            List of RankedSignal, ordered by rank (1=best).
        """
        if not signals:
            return []

        if len(signals) == 1:
            s = signals[0]
            return [RankedSignal(
                symbol=s["symbol"],
                direction=s["direction"],
                ev_score=s.get("ev_score", 0.0),
                rank=1,
                sizing_multiplier=1.0,
                edge_thesis="Single candidate",
                original_score=s.get("score", 0.0),
            )]

        # Take top N by EV score
        sorted_signals = sorted(signals, key=lambda s: s.get("ev_score", 0.0), reverse=True)
        top_signals = sorted_signals[: self.BATCH_SIZE]

        # If AI available, use it
        if self._ai is not None:
            try:
                ranked = await self._ai_rank(top_signals, portfolio_state or {})
                if ranked:
                    return ranked
            except Exception as e:
                logger.warning("AI ranker failed, falling back to EV ordering: %s", e)

        # Fallback: EV ordering with default sizing
        return [
            RankedSignal(
                symbol=s["symbol"],
                direction=s["direction"],
                ev_score=s.get("ev_score", 0.0),
                rank=i + 1,
                sizing_multiplier=1.0,
                edge_thesis="AI unavailable, EV ranking",
                original_score=s.get("score", 0.0),
            )
            for i, s in enumerate(top_signals)
        ]

    async def _ai_rank(
        self,
        signals: list[dict[str, Any]],
        portfolio_state: dict[str, Any],
    ) -> list[RankedSignal] | None:
        """Call AI to rank signals."""
        # Format signal list for prompt
        signal_lines = []
        for i, s in enumerate(signals):
            signal_lines.append(
                f"{i+1}. {s['symbol']} {s['direction']} "
                f"(score={s.get('score', 0):.1f}, ev={s.get('ev_score', 0):.4f}, "
                f"regime={s.get('regime', 'UNKNOWN')})"
            )

        prompt = RANKER_PROMPT.format(
            n_signals=len(signals),
            open_positions=portfolio_state.get("open_positions", "none"),
            daily_pnl=portfolio_state.get("daily_pnl", "$0"),
            available_capital=portfolio_state.get("available_capital", "unknown"),
            session_context=portfolio_state.get("session", "unknown"),
            macro_regime=portfolio_state.get("macro_regime", "unknown"),
            signal_list="\n".join(signal_lines),
        )

        # Call AI with timeout
        try:
            response = await asyncio.wait_for(
                self._ai.ask(prompt, max_tokens=500),
                timeout=10.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("AI rank call failed: %s", e)
            return None

        # Track cost (estimate tokens from prompt + response)
        input_tokens = len(prompt) // 4  # rough estimate: 1 token ≈ 4 chars
        output_tokens = len(response) // 4 if response else 0
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cost_usd += (
            (input_tokens / 1000) * self.COST_PER_1K_INPUT
            + (output_tokens / 1000) * self.COST_PER_1K_OUTPUT
        )
        self._call_count += 1

        # Parse response
        return self._parse_ranking(response, signals)

    def get_cost_summary(self) -> dict[str, Any]:
        """Get AI cost tracking summary."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost_usd, 6),
            "call_count": self._call_count,
            "avg_cost_per_call": round(
                self._total_cost_usd / self._call_count, 6
            ) if self._call_count > 0 else 0.0,
        }

    def _parse_ranking(
        self, response: str, signals: list[dict[str, Any]]
    ) -> list[RankedSignal] | None:
        """Parse AI response into RankedSignal list."""
        try:
            # Extract JSON from response
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            data = json.loads(text)
            if not isinstance(data, list):
                return None

            # Build lookup by symbol
            signal_map = {s["symbol"]: s for s in signals}

            ranked = []
            for item in data:
                sym = item.get("symbol", "")
                if sym not in signal_map:
                    continue

                s = signal_map[sym]
                ranked.append(RankedSignal(
                    symbol=sym,
                    direction=s["direction"],
                    ev_score=s.get("ev_score", 0.0),
                    rank=item.get("rank", len(ranked) + 1),
                    sizing_multiplier=max(0.5, min(1.5, item.get("sizing_multiplier", 1.0))),
                    edge_thesis=item.get("edge_thesis", ""),
                    original_score=s.get("score", 0.0),
                ))

            # Add any signals AI missed (shouldn't happen, but defensive)
            ranked_symbols = {r.symbol for r in ranked}
            for s in signals:
                if s["symbol"] not in ranked_symbols:
                    ranked.append(RankedSignal(
                        symbol=s["symbol"],
                        direction=s["direction"],
                        ev_score=s.get("ev_score", 0.0),
                        rank=len(ranked) + 1,
                        sizing_multiplier=1.0,
                        edge_thesis="AI missed this signal",
                        original_score=s.get("score", 0.0),
                    ))

            return ranked

        except Exception as e:
            logger.debug("Failed to parse AI ranking: %s", e)
            return None
