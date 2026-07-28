"""Unit tests for background loops."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestRankingRefreshLoop:
    """Test ranking_refresh_loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_once(self):
        """Test loop executes one iteration without error."""
        from app.consumer.loops.ranking_refresh import ranking_refresh_loop
        
        mock_trade_store = AsyncMock()
        mock_trade_store.get_recent_trades.return_value = [
            {"realized_pnl": 100},
            {"realized_pnl": -50},
        ]
        
        mock_ranking_engine = MagicMock()
        mock_ranking_engine.evaluate.return_value = MagicMock(value="PROMOTE")
        
        mock_redis = AsyncMock()
        shutdown_event = asyncio.Event()
        
        # Set shutdown immediately after first iteration
        async def run_once():
            # Let loop start, then shutdown
            await asyncio.sleep(0.01)
            shutdown_event.set()
            await asyncio.sleep(0.01)
        
        task = asyncio.create_task(ranking_refresh_loop(
            trade_store=mock_trade_store,
            ranking_engine=mock_ranking_engine,
            redis_client=mock_redis,
            shutdown_event=shutdown_event,
            interval_s=0,
        ))
        await run_once()
        await asyncio.sleep(0.05)
        
        # Verify loop ran
        assert mock_trade_store.get_recent_trades.called


class TestEloRefreshLoop:
    """Test elo_refresh_loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_once(self):
        """Test loop executes one iteration without error."""
        from app.consumer.loops.elo_refresh import elo_refresh_loop
        
        mock_trade_store = AsyncMock()
        mock_trade_store.get_recent_trades.return_value = [
            {"regime": "TREND_BULL", "side": "LONG", "realized_pnl": 100},
        ]
        
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        shutdown_event = asyncio.Event()
        
        async def run_once():
            await asyncio.sleep(0.01)
            shutdown_event.set()
            await asyncio.sleep(0.01)
        
        task = asyncio.create_task(elo_refresh_loop(
            trade_store=mock_trade_store,
            redis_client=mock_redis,
            shutdown_event=shutdown_event,
            interval_s=0,
        ))
        await run_once()
        await asyncio.sleep(0.05)
        
        assert mock_trade_store.get_recent_trades.called


class TestGateCalibrationLoop:
    """Test gate_calibration_loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_once(self):
        """Test loop executes one iteration without error."""
        from app.consumer.loops.gate_calibration import gate_calibration_loop
        
        mock_trade_store = AsyncMock()
        mock_trade_store.get_recent_trades.return_value = [
            {"realized_pnl": 100, "expected_value": 0.05},
            {"realized_pnl": 50, "expected_value": 0.03},
        ]
        
        mock_redis = AsyncMock()
        shutdown_event = asyncio.Event()
        
        async def run_once():
            await asyncio.sleep(0.01)
            shutdown_event.set()
            await asyncio.sleep(0.01)
        
        task = asyncio.create_task(gate_calibration_loop(
            trade_store=mock_trade_store,
            redis_client=mock_redis,
            shutdown_event=shutdown_event,
            interval_s=0,
        ))
        await run_once()
        await asyncio.sleep(0.05)
        
        assert mock_trade_store.get_recent_trades.called


class TestWalletMetricsLoop:
    """Test wallet_metrics_loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_once(self):
        """Test loop executes one iteration without error."""
        from app.consumer.loops.wallet_metrics import wallet_metrics_loop
        
        mock_bybit = AsyncMock()
        mock_bybit.get_wallet_balance.return_value = {"available": 1000, "balance": 5000}
        
        mock_position_store = AsyncMock()
        mock_position_store.list_all.return_value = []
        
        mock_redis = AsyncMock()
        mock_redis.get.return_value = b'5'
        
        shutdown_event = asyncio.Event()
        
        async def run_once():
            await asyncio.sleep(0.01)
            shutdown_event.set()
            await asyncio.sleep(0.01)
        
        task = asyncio.create_task(wallet_metrics_loop(
            bybit=mock_bybit,
            position_store=mock_position_store,
            redis_client=mock_redis,
            shutdown_event=shutdown_event,
            interval_s=0,
        ))
        await run_once()
        await asyncio.sleep(0.05)
        
        assert mock_position_store.list_all.called


class TestPositionExitLoop:
    """Test position_exit_loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_once(self):
        """Test loop executes one iteration without error."""
        from app.consumer.loops.position_exit import position_exit_loop
        
        mock_bybit = AsyncMock()
        mock_bybit.get_ticker.return_value = {"last": "50000"}
        
        mock_position_store = AsyncMock()
        mock_position_store.list_all.return_value = []
        
        mock_trade_store = AsyncMock()
        mock_redis = AsyncMock()
        
        shutdown_event = asyncio.Event()
        
        async def run_once():
            await asyncio.sleep(0.01)
            shutdown_event.set()
            await asyncio.sleep(0.01)
        
        task = asyncio.create_task(position_exit_loop(
            bybit=mock_bybit,
            position_store=mock_position_store,
            trade_store=mock_trade_store,
            redis_client=mock_redis,
            shutdown_event=shutdown_event,
            interval_s=0,
        ))
        await run_once()
        await asyncio.sleep(0.05)
        
        assert mock_position_store.list_all.called
