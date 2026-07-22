"""Decision Policy Plugin System (Sprint 6).

Separates governance rules from predictive trading models.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from loguru import logger

from app.core.decision_context import DecisionContext
from app.core.portfolio_snapshot import PortfolioSnapshot


class IPolicy(ABC):
    """Interface for governance rules."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def evaluate(self, context: DecisionContext, portfolio: PortfolioSnapshot) -> str | None:
        """Return a rejection reason (str) if the trade should be blocked, else None."""
        pass


class MaxExposurePolicy(IPolicy):
    @property
    def name(self) -> str:
        return "max_exposure"

    def evaluate(self, context: DecisionContext, portfolio: PortfolioSnapshot) -> str | None:
        # Example logic: if we already have 5 positions, reject
        if len(portfolio.positions) >= 5:
            return "Max portfolio positions reached (5)"
        return None


class DecisionPolicyManager:
    """Evaluates a DecisionContext against all registered policies."""

    def __init__(self) -> None:
        self.policies: list[IPolicy] = [
            MaxExposurePolicy(),
        ]

    def evaluate(self, context: DecisionContext, portfolio: PortfolioSnapshot) -> bool:
        """Run all policies. Return True if approved, False if rejected."""
        for policy in self.policies:
            rejection_reason = policy.evaluate(context, portfolio)
            if rejection_reason:
                logger.warning(f"Decision Rejected by Policy [{policy.name}]: {rejection_reason}")
                context.add_evidence("policy", -1.0, 100.0, f"Rejected: {rejection_reason}")
                return False

        logger.info(f"Decision Policy Approved for {context.symbol}")
        return True
