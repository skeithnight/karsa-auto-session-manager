"""Risk Gate — 3-layer sequential evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger


class RiskGate:
    """3-Layer Risk Gate: Liquidity, Spread Health, Circuit Breaker."""

    def __init__(
        self,
        min_24h_volume: Decimal = Decimal("1000000"),  # $1M minimum
        max_spread_pct: Decimal = Decimal("0.005"),  # 0.5% max spread
        daily_drawdown_limit: Decimal = Decimal("-0.02"),  # -2% — see CONTEXT.md Issue #2 for 2% vs 3% conflict
    ) -> None:
        logger.debug("RiskGate.__init__: entering")
        self.min_24h_volume = min_24h_volume
        self.max_spread_pct = max_spread_pct
        self.daily_drawdown_limit = daily_drawdown_limit
        self.daily_pnl = Decimal("0")
        logger.debug("RiskGate.__init__: returning")

    def check_liquidity(self, volume_24h: Decimal) -> bool:
        """Gate 1: 24h volume above threshold."""
        logger.debug(f"check_liquidity: entering volume_24h={volume_24h}")
        passed = volume_24h >= self.min_24h_volume
        if not passed:
            logger.warning(f"Liquidity gate FAILED: {volume_24h} < {self.min_24h_volume}")
        logger.debug(f"check_liquidity: returning {passed}")
        return passed

    def check_spread_health(self, bid_price: Decimal, ask_price: Decimal) -> bool:
        """Gate 2: Bid-ask spread within limits."""
        logger.debug(f"check_spread_health: entering bid={bid_price} ask={ask_price}")
        if bid_price == 0:
            logger.warning("Spread gate FAILED: bid_price is zero")
            logger.debug("check_spread_health: returning False (zero bid)")
            return False

        spread = (ask_price - bid_price) / bid_price
        passed = spread <= self.max_spread_pct
        if not passed:
            logger.warning(f"Spread gate FAILED: {spread:.4%} > {self.max_spread_pct:.4%}")
        logger.debug(f"check_spread_health: returning {passed}")
        return passed

    def check_circuit_breaker(self) -> bool:
        """Gate 3: Daily PnL drawdown check."""
        logger.debug("check_circuit_breaker: entering")
        passed = self.daily_pnl >= self.daily_drawdown_limit
        if not passed:
            logger.critical(f"Circuit breaker TRIGGERED: PnL {self.daily_pnl} < limit {self.daily_drawdown_limit}")
        logger.debug(f"check_circuit_breaker: returning {passed}")
        return passed

    def evaluate(
        self,
        volume_24h: Decimal,
        bid_price: Decimal,
        ask_price: Decimal,
    ) -> Dict[str, Any]:
        """Run all 3 gates sequentially. Returns decision dict."""
        logger.debug("evaluate: entering")
        gates = [
            ("liquidity", self.check_liquidity(volume_24h)),
            ("spread_health", self.check_spread_health(bid_price, ask_price)),
            ("circuit_breaker", self.check_circuit_breaker()),
        ]

        passed_gates = [name for name, ok in gates if ok]
        failed_gate = next((name for name, ok in gates if not ok), None)

        result = {
            "passed": failed_gate is None,
            "passed_gates": passed_gates,
            "failed_gate": failed_gate,
            "daily_pnl": str(self.daily_pnl),
        }
        logger.debug(f"evaluate: returning dict passed={result['passed']}")
        return result

    def update_pnl(self, pnl: Decimal) -> None:
        """Update daily PnL tracker."""
        logger.debug(f"update_pnl: entering pnl={pnl}")
        self.daily_pnl += pnl
        logger.info(f"Daily PnL updated: {self.daily_pnl}")
        logger.debug("update_pnl: returning None")

    def reset_daily(self) -> None:
        """Reset daily PnL (call at midnight UTC)."""
        logger.debug("reset_daily: entering")
        self.daily_pnl = Decimal("0")
        logger.info("Daily PnL reset")
        logger.debug("reset_daily: returning None")
