"""Decision Registry (Sprint 3).

Stores and retrieves DecisionContext instances in PostgreSQL for learning.
"""
from __future__ import annotations

import hashlib
import json

from loguru import logger

from app.core.decision_context import DecisionContext
from app.core.trade_store import TradeStore


class DecisionRegistry:
    """Historical record of probabilistic decisions."""

    def __init__(self, trade_store: TradeStore) -> None:
        self.store = trade_store

    async def record_decision(self, context: DecisionContext, latency_ms: int = 0) -> None:
        """Serialize and record a DecisionContext to PostgreSQL."""
        context_dict = context.to_dict()
        output_json = json.dumps(context_dict)

        # Create a deterministic hash of the state
        state_str = f"{context.symbol}_{context.regime.value}_{context.features.close}"
        input_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]

        try:
            await self.store.record_decision(
                symbol=context.symbol,
                decision_type="PROBABILISTIC_ENTRY",
                model="EvidenceCollector_v1",
                input_hash=input_hash,
                output_json=output_json,
                latency_ms=latency_ms
            )
            logger.info(f"DecisionRegistry: recorded decision for {context.symbol} (hash={input_hash})")
        except Exception as e:
            logger.error(f"DecisionRegistry: failed to record decision for {context.symbol}: {e}")
