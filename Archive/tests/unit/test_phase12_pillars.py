"""Tests for Phase 12 — Alpha Bridge, SOR regime-aware, PRM circuit breakers, SystemWatchdog."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.bridge import AlphaBridge
from app.watchdog.system_watchdog import (
    BALANCE_STALE_S,
    ORDERBOOK_STALE_S,
    REDIS_HALT_KEY,
    REDIS_WATCHDOG_STATUS_KEY,
    SystemWatchdog,
)

# ---------------------------------------------------------------------------
# Alpha Bridge
# ---------------------------------------------------------------------------


class TestAlphaBridge:
    def _make_bridge(self):
        engine = MagicMock()
        engine.evaluate = MagicMock(return_value=None)
        emitter = MagicMock()
        bridge = AlphaBridge(engine, emitter=emitter)
        return bridge, engine, emitter

    @pytest.mark.asyncio
    async def test_insufficient_candles(self) -> None:
        bridge, engine, _ = self._make_bridge()
        result = await bridge.generate_signal("BTC/USDT", [[1, 100, 101, 99, 100, 1000]] * 10)
        assert result is None
        engine.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_signal_generated(self) -> None:
        bridge, engine, _ = self._make_bridge()
        result = await bridge.generate_signal("BTC/USDT", [[1, 100, 101, 99, 100, 1000]] * 60)
        assert result is None

    @pytest.mark.asyncio
    async def test_signal_generated(self) -> None:
        bridge, engine, _ = self._make_bridge()
        mock_signal = MagicMock()
        mock_signal.score = 75.0
        mock_signal.regime.value = "TREND_BULL"
        mock_signal.direction = "LONG"
        engine.evaluate.return_value = mock_signal

        result = await bridge.generate_signal("BTC/USDT", [[1, 100, 101, 99, 100, 1000]] * 60)
        assert result is mock_signal

    @pytest.mark.asyncio
    async def test_get_last_signal(self) -> None:
        bridge, engine, _ = self._make_bridge()
        mock_signal = MagicMock()
        engine.evaluate.return_value = mock_signal
        await bridge.generate_signal("BTC/USDT", [[1, 100, 101, 99, 100, 1000]] * 60)
        assert bridge.get_last_signal("BTC/USDT") is mock_signal
        assert bridge.get_last_signal("ETH/USDT") is None

    @pytest.mark.asyncio
    async def test_evaluate_exception_handled(self) -> None:
        bridge, engine, _ = self._make_bridge()
        engine.evaluate.side_effect = RuntimeError("engine crash")
        result = await bridge.generate_signal("BTC/USDT", [[1, 100, 101, 99, 100, 1000]] * 60)
        assert result is None

    def test_global_state_extraction(self) -> None:
        prices = AlphaBridge._extract_global_prices({"global_vwap": 50000.0})
        assert prices == {"vwap": 50000.0}

    def test_global_state_none(self) -> None:
        assert AlphaBridge._extract_global_prices(None) is None

    def test_global_state_no_vwap(self) -> None:
        assert AlphaBridge._extract_global_prices({"some_field": 123}) is None

    def test_extract_field(self) -> None:
        gs = {"orderbook_delta": 0.5, "funding_rate": 0.001}
        assert AlphaBridge._extract_field(gs, "orderbook_delta") == 0.5
        assert AlphaBridge._extract_field(gs, "funding_rate") == 0.001
        assert AlphaBridge._extract_field(gs, "missing") is None

    def test_extract_field_none_state(self) -> None:
        assert AlphaBridge._extract_field(None, "orderbook_delta") is None


# ---------------------------------------------------------------------------
# SOR Regime-Aware
# ---------------------------------------------------------------------------


class TestSORRegimeAware:
    def test_spread_gate_constants(self) -> None:
        from app.execution.sor import (
            CHOP_RANGE_MAX_REPRICE,
            CHOP_RANGE_SPREAD_PCT,
            TREND_SPREAD_PCT,
        )

        assert CHOP_RANGE_MAX_REPRICE == 1
        assert CHOP_RANGE_SPREAD_PCT < TREND_SPREAD_PCT

    def test_sor_has_regime_methods(self) -> None:
        from app.execution.sor import SmartOrderRouter

        assert hasattr(SmartOrderRouter, "execute_regime_aware")
        assert hasattr(SmartOrderRouter, "_check_spread_gate")
        assert hasattr(SmartOrderRouter, "_try_post_only")


# ---------------------------------------------------------------------------
# PRM Circuit Breakers
# ---------------------------------------------------------------------------


class TestPRMCircuitBreakers:
    def test_prm_has_cb_methods(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        assert hasattr(PortfolioRiskManager, "_check_daily_loss_circuit_breaker")
        assert hasattr(PortfolioRiskManager, "_check_consecutive_loss_circuit_breaker")

    @pytest.mark.asyncio
    async def test_daily_cb_no_redis_passes(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        prm = PortfolioRiskManager(
            redis_client=None,
            position_store=None,
            trade_store=None,
            sector_mapping=None,
            bybit_client=None,
        )
        result = await prm._check_daily_loss_circuit_breaker()
        assert result.passed

    @pytest.mark.asyncio
    async def test_consecutive_cb_no_redis_passes(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        prm = PortfolioRiskManager(
            redis_client=None,
            position_store=None,
            trade_store=None,
            sector_mapping=None,
            bybit_client=None,
        )
        result = await prm._check_consecutive_loss_circuit_breaker()
        assert result.passed

    @pytest.mark.asyncio
    async def test_daily_cb_triggered(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "status": "TRIGGERED",
                    "reason": "daily loss -2.5% exceeded limit",
                }
            )
        )
        prm = PortfolioRiskManager(
            redis_client=redis,
            position_store=None,
            trade_store=None,
            sector_mapping=None,
            bybit_client=None,
        )
        result = await prm._check_daily_loss_circuit_breaker()
        assert not result.passed

    @pytest.mark.asyncio
    async def test_daily_cb_not_triggered_other_reason(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "status": "TRIGGERED",
                    "reason": "consecutive loss 5 exceeded",
                }
            )
        )
        prm = PortfolioRiskManager(
            redis_client=redis,
            position_store=None,
            trade_store=None,
            sector_mapping=None,
            bybit_client=None,
        )
        result = await prm._check_daily_loss_circuit_breaker()
        assert result.passed

    @pytest.mark.asyncio
    async def test_consecutive_cb_triggered(self) -> None:
        from app.risk.portfolio_risk_manager import PortfolioRiskManager

        redis = MagicMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "status": "TRIGGERED",
                    "reason": "consecutive loss 5 exceeded",
                }
            )
        )
        prm = PortfolioRiskManager(
            redis_client=redis,
            position_store=None,
            trade_store=None,
            sector_mapping=None,
            bybit_client=None,
        )
        result = await prm._check_consecutive_loss_circuit_breaker()
        assert not result.passed


# ---------------------------------------------------------------------------
# SystemWatchdog
# ---------------------------------------------------------------------------


class TestSystemWatchdog:
    def _make_watchdog(self):
        redis = MagicMock()
        redis.set = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        bybit = MagicMock()
        bybit.fetch_positions = AsyncMock(
            return_value=[
                {"symbol": "BTC/USDT", "side": "Long", "contracts": 0.5},
            ]
        )
        pos_store = MagicMock()
        pos_store.list_all = AsyncMock(
            return_value=[
                {"symbol": "BTC/USDT", "side": "Long", "amount": 0.5},
            ]
        )
        return (
            SystemWatchdog(
                redis_client=redis,
                bybit_client=bybit,
                position_store=pos_store,
            ),
            redis,
            bybit,
            pos_store,
        )

    @pytest.mark.asyncio
    async def test_position_match_no_desync(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        await wd._check_all()
        # Only status key written, not halt key
        status_written = any(call[0][0] == REDIS_WATCHDOG_STATUS_KEY for call in redis.set.call_args_list)
        assert status_written
        halt_written = any(call[0][0] == REDIS_HALT_KEY for call in redis.set.call_args_list)
        assert not halt_written

    @pytest.mark.asyncio
    async def test_position_desync_triggers_halt(self) -> None:
        wd, redis, _, pos_store = self._make_watchdog()
        pos_store.list_all = AsyncMock(return_value=[])
        await wd._check_all()
        halt_written = any(call[0][0] == REDIS_HALT_KEY for call in redis.set.call_args_list)
        assert halt_written
        assert wd._halted

    @pytest.mark.asyncio
    async def test_balance_staleness_detected(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        wd._last_balance_ts = time.time() - BALANCE_STALE_S - 1
        await wd._check_all()
        halt_written = any(call[0][0] == REDIS_HALT_KEY for call in redis.set.call_args_list)
        assert halt_written

    @pytest.mark.asyncio
    async def test_orderbook_staleness_detected(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        wd._last_orderbook_ts = time.time() - ORDERBOOK_STALE_S - 1
        await wd._check_all()
        halt_written = any(call[0][0] == REDIS_HALT_KEY for call in redis.set.call_args_list)
        assert halt_written

    @pytest.mark.asyncio
    async def test_no_halt_when_recent(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        wd.record_balance_update()
        wd.record_orderbook_update()
        await wd._check_all()
        halt_written = any(call[0][0] == REDIS_HALT_KEY for call in redis.set.call_args_list)
        assert not halt_written

    @pytest.mark.asyncio
    async def test_skip_when_already_halted(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        redis.get = AsyncMock(return_value="some_halt_reason")
        await wd._check_all()
        status_written = any(call[0][0] == REDIS_WATCHDOG_STATUS_KEY for call in redis.set.call_args_list)
        assert not status_written
        assert wd._halted

    def test_record_balance_update(self) -> None:
        wd, _, _, _ = self._make_watchdog()
        wd.record_balance_update()
        assert wd._last_balance_ts > 0

    def test_record_orderbook_update(self) -> None:
        wd, _, _, _ = self._make_watchdog()
        wd.record_orderbook_update()
        assert wd._last_orderbook_ts > 0

    def test_get_status_returns_copy(self) -> None:
        wd, _, _, _ = self._make_watchdog()
        status = wd.get_status()
        assert isinstance(status, dict)

    @pytest.mark.asyncio
    async def test_position_desync_exception(self) -> None:
        wd, _, bybit, _ = self._make_watchdog()
        bybit.fetch_positions = AsyncMock(side_effect=Exception("API down"))
        result = await wd._check_position_desync()
        assert result is None

    def test_balance_no_staleness_when_not_tracked(self) -> None:
        wd, _, _, _ = self._make_watchdog()
        assert wd._check_balance_staleness(time.time()) is None

    def test_orderbook_no_staleness_when_not_tracked(self) -> None:
        wd, _, _, _ = self._make_watchdog()
        assert wd._check_orderbook_staleness(time.time()) is None

    @pytest.mark.asyncio
    async def test_write_status(self) -> None:
        wd, redis, _, _ = self._make_watchdog()
        wd._status = {"last_check": "2024-01-01", "desyncs": [], "halted": False}
        await wd._write_status()
        redis.set.assert_called_once()
        assert redis.set.call_args[0][0] == REDIS_WATCHDOG_STATUS_KEY
