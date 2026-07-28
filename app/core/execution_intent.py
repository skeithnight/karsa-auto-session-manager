"""Immutable ExecutionIntent (v3.5 Prototype).

Replaces mutable position state mutations with immutable intent objects.
Every change creates a new version, making replay and auditing trivial.

Design Principles:
- Intent is a contract, not a command
- Never mutate — every change creates Intent v2, v3, etc.
- Contains goals and constraints, not implementation details
- SOR chooses execution strategy, APM chooses lifecycle actions

Example:
    intent = ExecutionIntent(
        symbol="BTCUSDT",
        side="LONG",
        quantity=Decimal("0.001"),
        goal=IntentGoal.OPEN_POSITION,
        constraints=IntentConstraints(
            max_slippage_pct=Decimal("0.003"),
            maker_preferred=True,
            time_budget_seconds=12,
        ),
    )

    # Later, create updated version
    intent_v2 = intent.with_updates(
        goal=IntentGoal.REDUCE_EXPOSURE,
        target_exposure=Decimal("0.5"),
        reason="Take partial profit",
    )
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class IntentGoal(Enum):
    """What the intent aims to achieve."""
    OPEN_POSITION = "open_position"
    CLOSE_POSITION = "close_position"
    REDUCE_EXPOSURE = "reduce_exposure"
    INCREASE_EXPOSURE = "increase_exposure"
    MOVE_STOP = "move_stop"
    MOVE_TARGET = "move_target"
    TRAIL_STOP = "trail_stop"
    HEDGE = "hedge"
    REBALANCE = "rebalance"


class IntentUrgency(Enum):
    """How quickly the intent must be executed."""
    LOW = "low"           # Best effort, maker preferred
    NORMAL = "normal"     # Standard execution
    HIGH = "high"         # Speed matters, accept some slippage
    CRITICAL = "critical" # Must fill immediately, market order


@dataclass(frozen=True)
class IntentConstraints:
    """Execution constraints — what the executor must respect.

    These are hard limits, not preferences.
    """
    max_slippage_pct: Decimal = Decimal("0.005")  # 0.5%
    max_slippage_bps: int = 50
    maker_preferred: bool = True
    time_budget_seconds: float = 30.0
    cancel_after_seconds: float | None = None
    min_fill_size: Decimal | None = None
    max_fill_size: Decimal | None = None
    allow_partial_fills: bool = True
    allow_market_orders: bool = True
    post_only: bool = False
    reduce_only: bool = False
    close_on_fail: bool = False  # Close position if execution fails

    @property
    def effective_max_slippage(self) -> Decimal:
        """Return whichever slippage limit is tighter."""
        bps_limit = Decimal(self.max_slippage_bps) / Decimal("10000")
        return min(self.max_slippage_pct, bps_limit)


@dataclass(frozen=True)
class IntentResult:
    """Outcome of intent execution — immutable record."""
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    slippage_bps: float = 0.0
    maker_fill: bool = False
    fill_time_ms: float = 0.0
    order_ids: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Did the intent execute successfully?"""
        return self.error is None and self.filled_quantity > 0

    @property
    def notional_value(self) -> Decimal:
        """Total value of fills."""
        return self.filled_quantity * self.average_price


@dataclass(frozen=True)
class ExecutionIntent:
    """Immutable execution intent — the contract between intelligence and execution.

    Attributes:
        intent_id: Unique identifier
        version: Version number (1, 2, 3, ...)
        parent_id: Previous version's intent_id (for audit trail)
        symbol: Trading pair
        side: LONG or SHORT
        quantity: Target quantity
        goal: What we're trying to achieve
        urgency: How quickly to execute
        constraints: Hard execution limits
        result: Execution outcome (None if not yet executed)
        reason: Why this intent was created
        created_at: Timestamp
        metadata: Additional context
    """
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    version: int = 1
    parent_id: str | None = None
    symbol: str = ""
    side: str = ""  # "LONG" or "SHORT"
    quantity: Decimal = Decimal("0")
    goal: IntentGoal = IntentGoal.OPEN_POSITION
    urgency: IntentUrgency = IntentUrgency.NORMAL
    constraints: IntentConstraints = field(default_factory=IntentConstraints)
    result: IntentResult | None = None
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    executed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Position-specific fields (for lifecycle management)
    current_exposure: Decimal | None = None
    target_exposure: Decimal | None = None
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    trail_distance: Decimal | None = None

    def with_updates(self, **kwargs: Any) -> ExecutionIntent:
        """Create a new version with updates. Never mutates original.

        Args:
            **kwargs: Fields to update in the new version

        Returns:
            New ExecutionIntent with incremented version

        Example:
            intent_v2 = intent.with_updates(
                goal=IntentGoal.REDUCE_EXPOSURE,
                target_exposure=Decimal("0.5"),
                reason="Take partial profit",
            )
        """
        # Build new fields dict
        current_dict = {
            "intent_id": self.intent_id,
            "version": self.version,
            "parent_id": self.parent_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "goal": self.goal,
            "urgency": self.urgency,
            "constraints": self.constraints,
            "result": self.result,
            "reason": self.reason,
            "created_at": self.created_at,
            "executed_at": self.executed_at,
            "metadata": self.metadata,
            "current_exposure": self.current_exposure,
            "target_exposure": self.target_exposure,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "trail_distance": self.trail_distance,
        }

        # Apply updates
        current_dict.update(kwargs)

        # Increment version, set parent
        current_dict["version"] = self.version + 1
        current_dict["parent_id"] = self.intent_id

        # Generate new intent_id for the new version
        current_dict["intent_id"] = uuid.uuid4().hex[:12]

        return ExecutionIntent(**current_dict)

    def mark_executed(self, result: IntentResult) -> ExecutionIntent:
        """Mark intent as executed with result. Creates new version.

        Args:
            result: Execution outcome

        Returns:
            New ExecutionIntent with result attached
        """
        return self.with_updates(
            result=result,
            executed_at=time.time(),
        )

    def with_slippage_limit(self, max_slippage_bps: int) -> ExecutionIntent:
        """Create version with tighter slippage limit.

        Args:
            max_slippage_bps: Maximum slippage in basis points

        Returns:
            New ExecutionIntent with updated constraints
        """
        new_constraints = IntentConstraints(
            max_slippage_pct=Decimal(max_slippage_bps) / Decimal("10000"),
            max_slippage_bps=max_slippage_bps,
            maker_preferred=self.constraints.maker_preferred,
            time_budget_seconds=self.constraints.time_budget_seconds,
            cancel_after_seconds=self.constraints.cancel_after_seconds,
            min_fill_size=self.constraints.min_fill_size,
            max_fill_size=self.constraints.max_fill_size,
            allow_partial_fills=self.constraints.allow_partial_fills,
            allow_market_orders=self.constraints.allow_market_orders,
            post_only=self.constraints.post_only,
            reduce_only=self.constraints.reduce_only,
            close_on_fail=self.constraints.close_on_fail,
        )
        return self.with_updates(constraints=new_constraints)

    def with_time_budget(self, seconds: float) -> ExecutionIntent:
        """Create version with shorter time budget.

        Args:
            seconds: Time budget in seconds

        Returns:
            New ExecutionIntent with updated constraints
        """
        new_constraints = IntentConstraints(
            max_slippage_pct=self.constraints.max_slippage_pct,
            max_slippage_bps=self.constraints.max_slippage_bps,
            maker_preferred=self.constraints.maker_preferred,
            time_budget_seconds=seconds,
            cancel_after_seconds=self.constraints.cancel_after_seconds,
            min_fill_size=self.constraints.min_fill_size,
            max_fill_size=self.constraints.max_fill_size,
            allow_partial_fills=self.constraints.allow_partial_fills,
            allow_market_orders=self.constraints.allow_market_orders,
            post_only=self.constraints.post_only,
            reduce_only=self.constraints.reduce_only,
            close_on_fail=self.constraints.close_on_fail,
        )
        return self.with_updates(constraints=new_constraints)

    def escalate_urgency(self) -> ExecutionIntent:
        """Escalate urgency to next level.

        Returns:
            New ExecutionIntent with higher urgency
        """
        urgency_order = [
            IntentUrgency.LOW,
            IntentUrgency.NORMAL,
            IntentUrgency.HIGH,
            IntentUrgency.CRITICAL,
        ]
        current_idx = urgency_order.index(self.urgency)
        if current_idx < len(urgency_order) - 1:
            new_urgency = urgency_order[current_idx + 1]
        else:
            new_urgency = self.urgency

        return self.with_updates(urgency=new_urgency)

    @property
    def is_expired(self) -> bool:
        """Has the intent's time budget elapsed?"""
        if self.executed_at is not None:
            return False
        elapsed = time.time() - self.created_at
        return elapsed > self.constraints.time_budget_seconds

    @property
    def age_seconds(self) -> float:
        """How old is this intent?"""
        return time.time() - self.created_at

    @property
    def version_chain(self) -> list[str]:
        """Get the chain of intent IDs from this version back to v1.

        Note: This requires parent_id links. In practice, you'd store
        the full chain in a list. This is a simplified version.
        """
        return [self.intent_id]  # Would need DB lookup for full chain

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging/storage."""
        return {
            "intent_id": self.intent_id,
            "version": self.version,
            "parent_id": self.parent_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "goal": self.goal.value,
            "urgency": self.urgency.value,
            "reason": self.reason,
            "created_at": self.created_at,
            "executed_at": self.executed_at,
            "is_expired": self.is_expired,
            "age_seconds": round(self.age_seconds, 3),
            "constraints": {
                "max_slippage_pct": str(self.constraints.max_slippage_pct),
                "max_slippage_bps": self.constraints.max_slippage_bps,
                "maker_preferred": self.constraints.maker_preferred,
                "time_budget_seconds": self.constraints.time_budget_seconds,
                "post_only": self.constraints.post_only,
                "reduce_only": self.constraints.reduce_only,
            },
            "current_exposure": str(self.current_exposure) if self.current_exposure else None,
            "target_exposure": str(self.target_exposure) if self.target_exposure else None,
            "stop_loss_price": str(self.stop_loss_price) if self.stop_loss_price else None,
            "take_profit_price": str(self.take_profit_price) if self.take_profit_price else None,
            "result": {
                "filled_quantity": str(self.result.filled_quantity),
                "average_price": str(self.result.average_price),
                "total_fees": str(self.result.total_fees),
                "slippage_bps": self.result.slippage_bps,
                "maker_fill": self.result.maker_fill,
                "fill_time_ms": self.result.fill_time_ms,
                "order_ids": self.result.order_ids,
                "error": self.result.error,
            } if self.result else None,
            "metadata": self.metadata,
        }


class IntentHistory:
    """Audit trail for intent versions.

    Tracks the full history of an intent through its version chain.
    """

    def __init__(self) -> None:
        self._intents: dict[str, ExecutionIntent] = {}  # intent_id -> intent
        self._chains: dict[str, list[str]] = {}  # root_id -> [intent_ids in order]

    def add(self, intent: ExecutionIntent) -> None:
        """Add an intent to the history.

        Args:
            intent: The intent to track
        """
        self._intents[intent.intent_id] = intent

        # Find or create chain
        root_id = self._find_root(intent)
        if root_id not in self._chains:
            self._chains[root_id] = []

        # Insert in version order
        chain = self._chains[root_id]
        if intent.intent_id not in chain:
            chain.append(intent.intent_id)
            chain.sort(key=lambda x: self._intents[x].version)

    def get_version(self, intent_id: str) -> ExecutionIntent | None:
        """Get a specific intent version."""
        return self._intents.get(intent_id)

    def get_chain(self, intent_id: str) -> list[ExecutionIntent]:
        """Get the full version chain for an intent.

        Args:
            intent_id: Any intent ID in the chain

        Returns:
            List of intents in version order (v1, v2, v3, ...)
        """
        root_id = self._find_root(self._intents[intent_id])
        chain = self._chains.get(root_id, [])
        return [self._intents[iid] for iid in chain if iid in self._intents]

    def get_latest(self, intent_id: str) -> ExecutionIntent:
        """Get the latest version of an intent.

        Args:
            intent_id: Any intent ID in the chain

        Returns:
            The latest version
        """
        chain = self.get_chain(intent_id)
        return chain[-1] if chain else self._intents[intent_id]

    def _find_root(self, intent: ExecutionIntent) -> str:
        """Walk up parent chain to find root intent."""
        current = intent
        while current.parent_id and current.parent_id in self._intents:
            current = self._intents[current.parent_id]
        return current.intent_id

    def get_all_chains(self) -> dict[str, list[ExecutionIntent]]:
        """Get all intent chains."""
        return {
            root_id: [self._intents[iid] for iid in chain if iid in self._intents]
            for root_id, chain in self._chains.items()
        }

    def clear(self) -> None:
        """Clear all history."""
        self._intents.clear()
        self._chains.clear()
