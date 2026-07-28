"""Tests for ExecutionIntent (v3.5 prototype)."""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.core.execution_intent import (
    ExecutionIntent,
    IntentConstraints,
    IntentGoal,
    IntentHistory,
    IntentResult,
    IntentUrgency,
)


class TestExecutionIntent:
    """Tests for immutable ExecutionIntent."""

    def test_create_intent(self):
        """Create a basic intent."""
        intent = ExecutionIntent(
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("0.001"),
            goal=IntentGoal.OPEN_POSITION,
        )
        assert intent.symbol == "BTCUSDT"
        assert intent.side == "LONG"
        assert intent.version == 1
        assert intent.parent_id is None

    def test_with_updates_creates_new_version(self):
        """with_updates should create a new version."""
        intent = ExecutionIntent(
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("0.001"),
        )
        intent_v2 = intent.with_updates(
            goal=IntentGoal.REDUCE_EXPOSURE,
            reason="Take profit",
        )

        # Original unchanged
        assert intent.goal == IntentGoal.OPEN_POSITION
        assert intent.version == 1

        # New version
        assert intent_v2.goal == IntentGoal.REDUCE_EXPOSURE
        assert intent_v2.version == 2
        assert intent_v2.parent_id == intent.intent_id
        assert intent_v2.intent_id != intent.intent_id

    def test_immutability(self):
        """Intent should be immutable (frozen dataclass)."""
        intent = ExecutionIntent(symbol="BTCUSDT")
        with pytest.raises(AttributeError):
            intent.symbol = "ETHUSDT"  # type: ignore

    def test_mark_executed(self):
        """mark_executed should create version with result."""
        intent = ExecutionIntent(symbol="BTCUSDT")
        result = IntentResult(
            filled_quantity=Decimal("0.001"),
            average_price=Decimal("50000"),
            maker_fill=True,
        )
        executed = intent.mark_executed(result)

        assert executed.result is not None
        assert executed.result.is_success
        assert executed.executed_at is not None
        assert executed.version == 2

    def test_escalate_urgency(self):
        """escalate_urgency should increase urgency level."""
        intent = ExecutionIntent(urgency=IntentUrgency.LOW)
        escalated = intent.escalate_urgency()

        assert escalated.urgency == IntentUrgency.NORMAL
        assert intent.urgency == IntentUrgency.LOW  # Original unchanged

    def test_escalate_urgency_max(self):
        """escalate_urgency at max should stay at max."""
        intent = ExecutionIntent(urgency=IntentUrgency.CRITICAL)
        escalated = intent.escalate_urgency()

        assert escalated.urgency == IntentUrgency.CRITICAL

    def test_with_slippage_limit(self):
        """with_slippage_limit should update constraints."""
        intent = ExecutionIntent()
        updated = intent.with_slippage_limit(25)  # 25 bps

        assert updated.constraints.max_slippage_bps == 25
        assert updated.constraints.max_slippage_pct == Decimal("0.0025")

    def test_with_time_budget(self):
        """with_time_budget should update constraints."""
        intent = ExecutionIntent()
        updated = intent.with_time_budget(5.0)

        assert updated.constraints.time_budget_seconds == 5.0

    def test_is_expired(self):
        """is_expired should check time budget."""
        intent = ExecutionIntent(
            constraints=IntentConstraints(time_budget_seconds=0.1),
            created_at=time.time() - 1.0,  # 1 second ago
        )
        assert intent.is_expired

    def test_is_not_expired_when_executed(self):
        """Executed intents should not be expired."""
        intent = ExecutionIntent(
            constraints=IntentConstraints(time_budget_seconds=0.1),
            created_at=time.time() - 1.0,
            executed_at=time.time(),
        )
        assert not intent.is_expired

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        intent = ExecutionIntent(
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("0.001"),
            goal=IntentGoal.OPEN_POSITION,
        )
        d = intent.to_dict()

        assert d["symbol"] == "BTCUSDT"
        assert d["side"] == "LONG"
        assert d["quantity"] == "0.001"
        assert d["goal"] == "open_position"
        assert d["version"] == 1

    def test_version_chain_multiple(self):
        """Multiple updates should create proper version chain."""
        intent = ExecutionIntent(symbol="BTCUSDT")
        v2 = intent.with_updates(reason="v2")
        v3 = v2.with_updates(reason="v3")

        assert intent.version == 1
        assert v2.version == 2
        assert v3.version == 3
        assert v2.parent_id == intent.intent_id
        assert v3.parent_id == v2.intent_id


class TestIntentConstraints:
    """Tests for IntentConstraints."""

    def test_default_constraints(self):
        """Default constraints should be reasonable."""
        c = IntentConstraints()
        assert c.max_slippage_pct == Decimal("0.005")
        assert c.maker_preferred is True
        assert c.time_budget_seconds == 30.0

    def test_effective_max_slippage(self):
        """effective_max_slippage should return tighter limit."""
        c = IntentConstraints(
            max_slippage_pct=Decimal("0.01"),  # 100 bps
            max_slippage_bps=25,               # 25 bps
        )
        assert c.effective_max_slippage == Decimal("0.0025")


class TestIntentResult:
    """Tests for IntentResult."""

    def test_success_result(self):
        """Successful result should have is_success=True."""
        result = IntentResult(
            filled_quantity=Decimal("0.001"),
            average_price=Decimal("50000"),
        )
        assert result.is_success
        assert result.notional_value == Decimal("50")

    def test_failed_result(self):
        """Failed result should have is_success=False."""
        result = IntentResult(error="Order rejected")
        assert not result.is_success

    def test_empty_result(self):
        """Zero quantity result should have is_success=False."""
        result = IntentResult()
        assert not result.is_success


class TestIntentHistory:
    """Tests for IntentHistory audit trail."""

    def test_add_and_get(self):
        """Should track intent versions."""
        history = IntentHistory()
        intent = ExecutionIntent(symbol="BTCUSDT")
        history.add(intent)

        assert history.get_version(intent.intent_id) is not None

    def test_get_chain(self):
        """Should return full version chain."""
        history = IntentHistory()
        intent = ExecutionIntent(symbol="BTCUSDT")
        v2 = intent.with_updates(reason="v2")
        v3 = v2.with_updates(reason="v3")

        history.add(intent)
        history.add(v2)
        history.add(v3)

        chain = history.get_chain(v3.intent_id)
        assert len(chain) == 3
        assert chain[0].version == 1
        assert chain[2].version == 3

    def test_get_latest(self):
        """Should return latest version."""
        history = IntentHistory()
        intent = ExecutionIntent(symbol="BTCUSDT")
        v2 = intent.with_updates(reason="v2")

        history.add(intent)
        history.add(v2)

        latest = history.get_latest(intent.intent_id)
        assert latest.version == 2

    def test_clear(self):
        """clear should remove all history."""
        history = IntentHistory()
        history.add(ExecutionIntent(symbol="BTCUSDT"))
        history.clear()

        assert len(history.get_all_chains()) == 0
