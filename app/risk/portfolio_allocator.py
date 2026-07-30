"""Portfolio EV Allocator — rank and allocate capital across opportunities.

Instead of isolated per-symbol yes/no decisions, this module:
  1. Collects all signals that pass hard safety gates
  2. Ranks by Expected Value (EV)
  3. Allocates capital across the top-N opportunities proportionally

Usage:
    allocator = PortfolioAllocator(max_positions=5, max_risk_per_trade=0.02)
    allocated = allocator.allocate([signal1, signal2, signal3], wallet_balance)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """EV-based portfolio allocator.

    Ranks signals by expected value and allocates capital proportionally.
    Replaces the old "first signal that passes the gate" approach.
    """

    def __init__(
        self,
        max_positions: int = 5,
        max_risk_per_trade: Decimal = Decimal("0.02"),
        max_correlated: int = 2,
    ) -> None:
        """
        Args:
            max_positions: Maximum concurrent open positions.
            max_risk_per_trade: Maximum risk per trade as fraction of wallet.
            max_correlated: Max positions in same sector.
        """
        self.max_positions = max_positions
        self.max_risk_per_trade = max_risk_per_trade
        self.max_correlated = max_correlated

    def allocate(
        self,
        signals: list[Any],  # List of TradeSignal objects
        wallet_balance: Decimal,
        open_positions: int = 0,
    ) -> list[dict[str, Any]]:
        """Rank signals by EV and allocate capital across top-N.

        Args:
            signals: TradeSignal objects that passed hard safety gates.
            wallet_balance: Current wallet balance in USDT.
            open_positions: Number of currently open positions.

        Returns:
            List of allocation dicts with adjusted amounts, ranked by EV.
        """
        if not signals or wallet_balance <= 0:
            return []

        # Sort by EV descending
        ranked = sorted(signals, key=lambda s: getattr(s, "expected_value", 0), reverse=True)

        # Filter: only positive EV
        positive_ev = [s for s in ranked if getattr(s, "expected_value", 0) > 0]

        if not positive_ev:
            logger.info("PortfolioAllocator: no positive-EV signals — staying flat")
            return []

        # How many new positions can we take?
        slots_available = max(0, self.max_positions - open_positions)
        if slots_available == 0:
            logger.info("PortfolioAllocator: max positions reached — no new entries")
            return []

        # Take top-N by EV
        top_n = positive_ev[:slots_available]

        # Allocate capital proportionally by EV
        total_ev = sum(getattr(s, "expected_value", 0) for s in top_n)
        if total_ev <= 0:
            return []

        allocations = []
        for signal in top_n:
            ev = getattr(signal, "expected_value", 0)
            ev_weight = ev / total_ev

            # Risk-based sizing: fraction of wallet * EV weight
            risk_budget = wallet_balance * self.max_risk_per_trade
            allocated_risk = risk_budget * Decimal(str(ev_weight))

            # Compute amount from risk budget and SL distance
            entry = getattr(signal, "entry_price", Decimal("0"))
            sl = getattr(signal, "sl_price", Decimal("0"))
            if entry > 0 and sl > 0 and entry != sl:
                risk_per_unit = abs(entry - sl)
                amount = (allocated_risk / risk_per_unit).quantize(Decimal("0.001"))
                # Cap at signal's original amount
                original_amount = getattr(signal, "amount", Decimal("0"))
                if original_amount > 0:
                    amount = min(amount, original_amount)
            else:
                amount = getattr(signal, "amount", Decimal("0"))

            if amount > 0:
                allocations.append({
                    "signal": signal,
                    "symbol": getattr(signal, "symbol", "?"),
                    "direction": getattr(signal, "direction", "?"),
                    "ev": ev,
                    "ev_weight": round(float(ev_weight), 4),
                    "allocated_risk_usd": round(float(allocated_risk), 2),
                    "amount": amount,
                    "entry_price": entry,
                    "sl_price": sl,
                })

        if allocations:
            logger.info(
                f"PortfolioAllocator: {len(allocations)} positions allocated "
                f"(top EV: {allocations[0]['symbol']}={allocations[0]['ev']:.4f})"
            )

        return allocations
