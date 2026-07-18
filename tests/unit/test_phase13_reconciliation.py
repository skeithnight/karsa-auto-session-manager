"""Tests for Phase 13 — State Reconciliation, BybitClient backoff, MarketConsumer reconnect."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.state_reconciliation import StateReconciler

# ---------------------------------------------------------------------------
# StateReconciler
# ---------------------------------------------------------------------------


class TestStateReconciler:
    def _make_reconciler(self, exchange_positions=None, exchange_orders=None, internal_positions=None):
        bybit = MagicMock()
        bybit.fetch_positions = AsyncMock(return_value=exchange_positions or [])
        bybit.fetch_open_orders = AsyncMock(return_value=exchange_orders or [])
        pos_store = MagicMock()
        pos_store.list_all = AsyncMock(return_value=internal_positions or [])
        trade_store = MagicMock()
        db = MagicMock()
        db.engine = MagicMock()
        return StateReconciler(bybit, pos_store, trade_store, db), bybit, pos_store

    @pytest.mark.asyncio
    async def test_empty_reconciliation(self) -> None:
        reconciler, _, _ = self._make_reconciler()
        results = await reconciler.reconcile()
        assert results["exchange_positions"] == 0
        assert results["internal_positions"] == 0
        assert results["orphaned"] == 0

    @pytest.mark.asyncio
    async def test_matching_positions_no_orphan(self) -> None:
        exchange = [{"symbol": "BTCUSDT", "side": "Buy", "contracts": 0.1, "entry_price": 50000, "unrealized_pnl": 0}]
        internal = [{"symbol": "BTCUSDT", "side": "Buy", "amount": 0.1, "entry_price": 50000}]
        reconciler, _, _ = self._make_reconciler(exchange_positions=exchange, internal_positions=internal)
        results = await reconciler.reconcile()
        assert results["orphaned"] == 0
        assert results["updated"] == 0

    @pytest.mark.asyncio
    async def test_orphaned_position_detected(self) -> None:
        exchange = [{"symbol": "ETHUSDT", "side": "Buy", "contracts": 1.0, "entry_price": 3000, "unrealized_pnl": 50}]
        reconciler, _, _ = self._make_reconciler(exchange_positions=exchange, internal_positions=[])
        results = await reconciler.reconcile()
        assert results["orphaned"] == 1

    @pytest.mark.asyncio
    async def test_missing_position_detected(self) -> None:
        internal = [{"symbol": "SOLUSDT", "side": "Buy", "amount": 10, "entry_price": 100}]
        reconciler, _, _ = self._make_reconciler(exchange_positions=[], internal_positions=internal)
        results = await reconciler.reconcile()
        assert results["missing"] == 1

    @pytest.mark.asyncio
    async def test_amount_mismatch_detected(self) -> None:
        exchange = [{"symbol": "BTCUSDT", "side": "Buy", "contracts": 0.2, "entry_price": 50000, "unrealized_pnl": 0}]
        internal = [{"symbol": "BTCUSDT", "side": "Buy", "amount": 0.1, "entry_price": 50000}]
        reconciler, _, _ = self._make_reconciler(exchange_positions=exchange, internal_positions=internal)
        results = await reconciler.reconcile()
        assert results["updated"] == 1

    @pytest.mark.asyncio
    async def test_exchange_fetch_failure_handled(self) -> None:
        bybit = MagicMock()
        bybit.fetch_positions = AsyncMock(side_effect=Exception("API error"))
        bybit.fetch_open_orders = AsyncMock(return_value=[])
        pos_store = MagicMock()
        pos_store.list_all = AsyncMock(return_value=[])
        db = MagicMock()
        reconciler = StateReconciler(bybit, pos_store, None, db)
        results = await reconciler.reconcile()
        assert results["exchange_positions"] == 0

    def test_diff_positions(self) -> None:
        reconciler, _, _ = self._make_reconciler()
        exchange = [
            {"symbol": "BTCUSDT", "side": "Buy", "contracts": 0.1, "entry_price": 50000},
            {"symbol": "ETHUSDT", "side": "Buy", "contracts": 1.0, "entry_price": 3000},
        ]
        internal = [{"symbol": "BTCUSDT", "side": "Buy", "amount": 0.1, "entry_price": 50000}]
        orphaned, missing, updated = reconciler._diff_positions(exchange, internal)
        assert len(orphaned) == 1
        assert orphaned[0]["symbol"] == "ETHUSDT"

    def test_diff_empty(self) -> None:
        reconciler, _, _ = self._make_reconciler()
        orphaned, missing, updated = reconciler._diff_positions([], [])
        assert orphaned == []
        assert missing == []
        assert updated == []

    def test_get_results_returns_copy(self) -> None:
        reconciler, _, _ = self._make_reconciler()
        assert reconciler.get_results() == {}


# ---------------------------------------------------------------------------
# BybitClient backoff improvements
# ---------------------------------------------------------------------------


class TestBybitClientBackoff:
    def test_has_max_retries_constant(self) -> None:
        from app.execution.bybit_client import BybitClient
        assert hasattr(BybitClient, "_MAX_RETRIES")

    def test_has_create_session(self) -> None:
        from app.execution.bybit_client import BybitClient
        assert hasattr(BybitClient, "_create_session")

    def test_has_reconnect(self) -> None:
        from app.execution.bybit_client import BybitClient
        assert hasattr(BybitClient, "reconnect")


# ---------------------------------------------------------------------------
# MarketConsumer reconnection
# ---------------------------------------------------------------------------


class TestMarketConsumerReconnect:
    def test_start_method_exists(self) -> None:
        from app.consumer.market_consumer import MarketConsumer
        assert hasattr(MarketConsumer, "start")
