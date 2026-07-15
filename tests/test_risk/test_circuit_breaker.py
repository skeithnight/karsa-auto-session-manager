"""Tests for Circuit Breaker."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone, timedelta


from app.risk.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def setup_method(self):
        self.cb = CircuitBreaker(
            daily_drawdown_limit=Decimal("-0.02"),
            max_consecutive_losses=3,
            loss_pause_minutes=60,
        )

    def test_update_pnl_no_trigger(self):
        assert self.cb.update_pnl(Decimal("100")) is False
        assert self.cb.halted is False

    def test_update_pnl_triggers(self):
        assert self.cb.update_pnl(Decimal("-0.03")) is True
        assert self.cb.halted is True
        assert "Drawdown breached" in self.cb.halt_reason

    def test_record_loss_no_pause(self):
        assert self.cb.record_loss() is False
        assert self.cb.consecutive_losses == 1

    def test_record_loss_triggers_pause(self):
        self.cb.record_loss()
        self.cb.record_loss()
        assert self.cb.record_loss() is True
        assert self.cb.paused_until is not None

    def test_record_win_resets(self):
        self.cb.record_loss()
        self.cb.record_loss()
        self.cb.record_win()
        assert self.cb.consecutive_losses == 0

    def test_usd_loss_cap_triggers(self):
        """max_daily_loss_usd enforced when set."""
        cb = CircuitBreaker(
            daily_drawdown_limit=Decimal("-200"),  # Loose limit so drawdown doesn't trigger
            max_daily_loss_usd=Decimal("100"),
        )
        assert cb.update_pnl(Decimal("-100")) is True
        assert cb.halted is True
        assert "USD loss cap" in cb.halt_reason

    def test_is_paused_false(self):
        assert self.cb.is_paused() is False

    def test_is_paused_true(self):
        self.cb.paused_until = datetime.now(timezone.utc) + timedelta(hours=1)
        assert self.cb.is_paused() is True

    def test_is_halted(self):
        assert self.cb.is_halted() is False
        self.cb.halted = True
        assert self.cb.is_halted() is True

    def test_reset(self):
        self.cb.halted = True
        self.cb.consecutive_losses = 3
        self.cb.reset()
        assert self.cb.halted is False
        assert self.cb.consecutive_losses == 0

    def test_get_state(self):
        state = self.cb.get_state()
        assert "halted" in state
        assert "daily_pnl" in state
        assert "consecutive_losses" in state
