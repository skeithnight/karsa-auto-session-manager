"""Risk Gate — 3-layer sequential evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

from app.risk.circuit_breaker import CircuitBreaker


class RiskGate:
    """3-Layer Risk Gate: Liquidity, Spread Health, Circuit Breaker.

    Gate 3 delegates to the shared CircuitBreaker instance to avoid
    divergent PnL tracking (fix #5)."""

    def __init__(
        self,
        min_liquidity_usd: Decimal = Decimal("10000"),  # $10K notional
        max_spread_pct: Decimal = Decimal("0.005"),  # 0.5% max spread
        circuit_breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.min_liquidity_usd = min_liquidity_usd
        self.max_spread_pct = max_spread_pct
        self.circuit_breaker = circuit_breaker

    def check_liquidity(self, notional_usd: Decimal) -> bool:
        """Gate 1: L1 notional depth above threshold."""
        passed = notional_usd >= self.min_liquidity_usd
        if not passed:
            logger.warning(f"Liquidity gate FAILED: ${notional_usd:,.2f} < ${self.min_liquidity_usd:,.2f}")
        return passed

    def check_spread_health(self, bid_price: Decimal, ask_price: Decimal) -> bool:
        """Gate 2: Bid-ask spread within limits."""
        if bid_price == 0:
            logger.warning("Spread gate FAILED: bid_price is zero")
            return False

        spread = (ask_price - bid_price) / bid_price
        passed = spread <= self.max_spread_pct
        if not passed:
            logger.warning(f"Spread gate FAILED: {spread:.4%} > {self.max_spread_pct:.4%}")
        return passed

    def check_circuit_breaker(self) -> bool:
        """Gate 3: Daily PnL drawdown check.

        Delegates to the shared CircuitBreaker instance when available.
        Returns True (pass) if no circuit_breaker wired."""
        if self.circuit_breaker:
            passed = not self.circuit_breaker.is_halted()
            if not passed:
                logger.critical(
                    f"Circuit breaker TRIGGERED: {self.circuit_breaker.halt_reason}"
                )
            return passed
        # Fallback: no CB wired, pass through
        return True

    def evaluate(
        self,
        volume_24h: Decimal,
        bid_price: Decimal,
        ask_price: Decimal,
    ) -> Dict[str, Any]:
        """Run all 3 gates sequentially. Returns decision dict."""
        mid_price = (bid_price + ask_price) / 2
        notional_usd = volume_24h * mid_price
        gates = [
            ("liquidity", self.check_liquidity(notional_usd)),
            ("spread_health", self.check_spread_health(bid_price, ask_price)),
            ("circuit_breaker", self.check_circuit_breaker()),
        ]

        passed_gates = [name for name, ok in gates if ok]
        failed_gate = next((name for name, ok in gates if not ok), None)

        return {
            "passed": failed_gate is None,
            "passed_gates": passed_gates,
            "failed_gate": failed_gate,
        }
