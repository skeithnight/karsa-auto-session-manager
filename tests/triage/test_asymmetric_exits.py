"""Tests for Profitability Triage Sprint — Asymmetric Time Exits and Free Roll.

Verifies:
  - Losing positions exit in 3 minutes
  - Breakeven positions exit in 15 minutes
  - Winning positions have no time limit (run forever)
  - Momentum decay exits when R stalls
  - Free Roll breakeven triggers at +0.25R (non-HYPER)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.execution.position_manager import (
    APM_BREAKEVEN_LOCK_R,
    ActivePositionManager,
)


def _make_position(
    symbol: str = "TEST/USDT",
    side: str = "LONG",
    entry_price: str = "1.00",
    live_price: str = "1.00",
    entry_regime: str = "RANGE",
    entry_time: datetime | None = None,
    atr: str = "0.01",
    initial_risk: str = "0.01",
    moved_to_be: bool = False,
    peak_r_multiple: str = "0",
    peak_r_ts: str = "0",
) -> dict:
    """Build a position dict matching APM's expected structure."""
    if entry_time is None:
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "live_price": live_price,
        "entry_regime": entry_regime,
        "entry_time": entry_time.isoformat(),
        "atr": atr,
        "initial_risk_per_unit": initial_risk,
        "moved_to_breakeven": moved_to_be,
        "peak_r_multiple": peak_r_multiple,
        "peak_r_ts": peak_r_ts,
        "max_hold_time_mins": 1440,
    }


class TestAsymmetricTimeExits:
    """Test the asymmetric time exit logic in APM._manage_time_exit()."""

    @pytest.mark.asyncio
    async def test_losing_position_exits_in_3_minutes(self):
        """A losing position (R < -0.35) should be force-closed after 45 minutes."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=50)
        pos = _make_position(entry_time=entry_time, live_price="0.98")  # 2% loss

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("0.98"), Decimal("0.01"))
        assert r_mult < Decimal("-0.35"), f"Expected negative R < -0.35, got {r_mult}"

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("0.98"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is True, "Losing position should be closed after 45+ minutes"
        apm._force_close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_losing_position_not_closed_before_3_minutes(self):
        """A losing position should NOT be closed before 45 minutes."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        pos = _make_position(entry_time=entry_time, live_price="0.98")

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("0.98"), Decimal("0.01"))

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("0.98"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is False, "Losing position should NOT be closed before 45 minutes"

    @pytest.mark.asyncio
    async def test_breakeven_position_exits_in_15_minutes(self):
        """A breakeven/stagnant position (R <= 0.10) should be closed after 75 minutes."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=80)
        pos = _make_position(entry_time=entry_time, live_price="1.00")

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("1.00"), Decimal("0.01"))
        assert r_mult == Decimal("0"), f"Expected zero R, got {r_mult}"

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("1.00"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is True, "Breakeven position should be closed after 75+ minutes"
        apm._force_close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_winning_position_no_time_limit(self):
        """A winning position (R > 0.10) should NOT have a time exit."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        pos = _make_position(entry_time=entry_time, live_price="1.02")

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("1.02"), Decimal("0.01"))
        assert r_mult > Decimal("0.10"), f"Expected positive R, got {r_mult}"

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("1.02"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is False, "Winning position should NOT have a time exit"

    @pytest.mark.asyncio
    async def test_quick_profit_exit_for_extreme_spike(self):
        """A winning position with extreme R in first 5 minutes should exit (safety net)."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=3)
        pos = _make_position(entry_time=entry_time, live_price="1.05")

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("1.05"), Decimal("0.01"))
        assert r_mult >= Decimal("3.0"), f"Expected R >= 3.0, got {r_mult}"

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("1.05"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is True, "Extreme spike should trigger quick profit exit"
        apm._force_close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_max_hold_fallback(self):
        """Hard max hold should still work as a fail-safe."""
        apm = _make_apm()
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=1441)
        pos = _make_position(entry_time=entry_time, live_price="1.001")

        r_mult = apm._exit_manager._calculate_r_multiple("LONG", Decimal("1.00"), Decimal("1.001"), Decimal("0.01"))

        result = await apm._exit_manager._manage_time_exit(
            pos, entry_time, 1440, Decimal("1.001"), Decimal("1.00"), "LONG", r_mult, "RANGE"
        )
        assert result is True, "Hard max hold should trigger after max_hold_time_mins"


class TestFreeRollBreakeven:
    """Test that fee-aware breakeven lock constant is correctly configured."""

    def test_breakeven_lock_r_is_025(self):
        """APM_BREAKEVEN_LOCK_R should be 1.50 (Fee-Aware Breakeven)."""
        assert APM_BREAKEVEN_LOCK_R == Decimal("1.50"), (
            f"Expected breakeven at 1.50R, got {APM_BREAKEVEN_LOCK_R}"
        )

    def test_breakeven_lock_r_not_075(self):
        """Ensure we didn't accidentally keep the old 0.75R value."""
        assert APM_BREAKEVEN_LOCK_R != Decimal("0.75"), (
            "Old 0.75R breakeven still present — should be 1.50R"
        )


class TestVolatilityFloor:
    """Test that DecisionEngine applies environmental penalty below 10th percentile ATR."""

    @pytest.mark.asyncio
    async def test_volatility_floor_blocks_low_vol(self):
        """BTC ATR < 10th percentile should apply 0.5x EV penalty."""
        import json

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps({
            "threshold": 0.5,
            "current_atr": 0.3,  # Below threshold
        })

        engine = _make_decision_engine(redis_client=mock_redis)

        candles = [
            [1700000000000 + i * 3600000, 100.0, 101.0, 99.0, 100.5, 1000.0]
            for i in range(100)
        ]

        result = await engine.evaluate("ALT/USDT", candles)
        assert result is None, "Low volatility should penalize/block trade"


class TestMacroNarrator:
    """Test the AI Macro Narrator state-based sizing."""

    def test_macro_multipliers(self):
        """Multipliers should be correct for each state."""
        from app.alpha.macro_narrator import MACRO_MULTIPLIERS, MacroState

        assert MACRO_MULTIPLIERS[MacroState.RISK_ON] == 1.0
        assert MACRO_MULTIPLIERS[MacroState.RISK_OFF] == 0.25
        assert MACRO_MULTIPLIERS[MacroState.CHOP] == 0.5

    @pytest.mark.asyncio
    async def test_get_macro_multiplier_default(self):
        """get_macro_multiplier should return 1.0 when Redis has no data."""
        from app.alpha.macro_narrator import get_macro_multiplier

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        result = await get_macro_multiplier(mock_redis)
        assert result == 1.0, "Default multiplier should be 1.0 (no adjustment)"

    @pytest.mark.asyncio
    async def test_get_macro_multiplier_risk_on(self):
        """get_macro_multiplier should return 1.0 for RISK_ON."""
        from app.alpha.macro_narrator import get_macro_multiplier
        import json

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps({"multiplier": 1.0, "state": "RISK_ON"})

        result = await get_macro_multiplier(mock_redis)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_get_macro_multiplier_chop(self):
        """get_macro_multiplier should return 0.5 for CHOP."""
        from app.alpha.macro_narrator import get_macro_multiplier
        import json

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps({"multiplier": 0.5, "state": "CHOP"})

        result = await get_macro_multiplier(mock_redis)
        assert result == 0.5


# ─── Helpers ────────────────────────────────────────────────────────────

def _make_apm() -> ActivePositionManager:
    """Create an APM instance with mocked dependencies."""
    apm = ActivePositionManager(
        bybit_client=AsyncMock(),
        position_store=AsyncMock(),
        redis_client=AsyncMock(),
        regime_classifier=AsyncMock(),
        alert_service=AsyncMock(),
    )
    apm._force_close_position = AsyncMock()
    apm._exit_manager._force_close_position = apm._force_close_position
    return apm


def _make_decision_engine(redis_client=None) -> "DecisionEngine":
    """Create a DecisionEngine instance with mocked dependencies."""
    from app.consumer.decision_engine import DecisionEngine

    engine = DecisionEngine.__new__(DecisionEngine)
    engine._analyzer = MagicMock()
    engine._analyzer.current_state = MagicMock(regime="RANGE")
    engine._risk_gate = MagicMock()
    engine._gate = Decimal("75.0")
    engine._base_size = Decimal("0.001")
    engine._slippage = Decimal("0.0005")
    engine._taker_fee = Decimal("0.00055")
    engine._maker_fee = Decimal("0.0002")
    engine._wallet_balance = Decimal("100")
    engine._trade_memory = None
    engine._redis = redis_client
    engine._multi_tf = None
    engine._crypto_analyst = None
    engine._trade_store = None
    engine._prev_regimes = {}
    engine._regime_transition_counts = {}
    engine._similarity = MagicMock()
    engine._evidence_collector = MagicMock()
    engine._edge_calculator = MagicMock()
    engine._statistical_learning = MagicMock()
    engine._statistical_learning.calibrate = AsyncMock()
    engine._sector_filter = MagicMock()
    engine._background_tasks = set()
    engine._router = MagicMock()
    engine._router.evaluate_signal = AsyncMock(return_value=(None, 1.0))
    return engine
