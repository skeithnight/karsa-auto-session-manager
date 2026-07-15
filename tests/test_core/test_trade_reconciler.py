"""Tests for TradeReconciler — trade history reconciliation with Bybit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.trade_reconciler import (
    TradeReconciler,
    _normalize_side,
    _safe_decimal,
)


@pytest.fixture
def mock_bybit():
    client = MagicMock()
    client._symbol_map = {"BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT"}
    client.get_executions = AsyncMock(return_value={"executions": [], "cursor": None})
    client.get_closed_pnl = AsyncMock(return_value={"closed_pnl": [], "cursor": None})
    return client


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_trades_since = AsyncMock(return_value=[])
    store.get_open_trade_by_symbol = AsyncMock(return_value=None)
    store.record_entry = AsyncMock()
    store.close_trade = AsyncMock()
    return store


@pytest.fixture
def mock_alert():
    alert = AsyncMock()
    return alert


@pytest.fixture
def reconciler(mock_bybit, mock_store, mock_alert):
    return TradeReconciler(
        bybit_client=mock_bybit,
        trade_store=mock_store,
        alert_service=mock_alert,
        lookback_hours=24,
    )


# ── Helpers ──────────────────────────────────────────────────


def _make_bybit_fill(
    symbol="BTCUSDT",
    side="Buy",
    price="64000.00",
    qty="0.001",
    order_id="order1",
    exec_time=None,
):
    """Create a Bybit execution record."""
    if exec_time is None:
        exec_time = int(
            (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000
        )
    return {
        "execId": "exec1",
        "symbol": symbol,
        "side": side,
        "execPrice": price,
        "execQty": qty,
        "execTime": str(exec_time),
        "orderId": order_id,
        "execFee": "0.01",
    }


def _make_local_trade(
    symbol="BTC/USDT",
    side="Buy",
    entry_price=Decimal("64000.00"),
    amount=Decimal("0.001"),
    entry_time=None,
    exit_time=None,
    trade_id=1,
    pnl=None,
):
    """Create a local trade record dict."""
    if entry_time is None:
        entry_time = datetime.now(timezone.utc) - timedelta(hours=1)
    return {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "entry_price": entry_price,
        "exit_price": None,
        "pnl": pnl,
        "regime": "TREND",
        "entry_time": entry_time,
        "exit_time": exit_time,
        "exit_reason": None,
    }


# ── Unit: helpers ────────────────────────────────────────────


class TestNormalizeSide:
    def test_buy_variants(self):
        assert _normalize_side("buy") == "Buy"
        assert _normalize_side("BUY") == "Buy"
        assert _normalize_side("long") == "Buy"
        assert _normalize_side("LONG") == "Buy"

    def test_sell_variants(self):
        assert _normalize_side("sell") == "Sell"
        assert _normalize_side("SELL") == "Sell"
        assert _normalize_side("short") == "Sell"
        assert _normalize_side("SHORT") == "Sell"

    def test_unknown_passthrough(self):
        assert _normalize_side("Something") == "Something"


class TestSafeDecimal:
    def test_valid(self):
        assert _safe_decimal("64000.50") == Decimal("64000.50")

    def test_invalid_returns_default(self):
        assert _safe_decimal("abc") == Decimal("0")
        assert _safe_decimal(None) == Decimal("0")

    def test_custom_default(self):
        assert _safe_decimal(None, "1") == Decimal("1")


# ── Unit: clean reconciliation ───────────────────────────────


class TestReconcileClean:
    @pytest.mark.asyncio
    async def test_no_bybit_data_no_local(self, reconciler, mock_bybit, mock_store):
        """Both empty — all clean."""
        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        assert report.bybit_fills_checked == 0
        assert report.local_trades_checked == 0
        assert len(report.discrepancies) == 0
        assert report.repairs_made == 0

    @pytest.mark.asyncio
    async def test_matching_fill_no_discrepancy(
        self, reconciler, mock_bybit, mock_store
    ):
        """Bybit fill matches local trade — no discrepancy."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=1)
        fill = _make_bybit_fill(exec_time=int(exec_time.timestamp() * 1000))
        local = _make_local_trade(entry_time=exec_time)

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])

        report = await reconciler.reconcile()

        assert report.bybit_fills_checked == 1
        assert len(report.discrepancies) == 0


# ── Unit: missing entry ──────────────────────────────────────


class TestMissingEntry:
    @pytest.mark.asyncio
    async def test_detects_missing_entry(self, reconciler, mock_bybit, mock_store):
        """Bybit fill with no local trade — missing_entry."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=2)
        fill = _make_bybit_fill(exec_time=int(exec_time.timestamp() * 1000))

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].kind == "missing_entry"
        assert report.discrepancies[0].symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_auto_repairs_missing_entry(self, reconciler, mock_bybit, mock_store):
        """Missing entry gets auto-repaired."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=2)
        fill = _make_bybit_fill(
            price="64000.00",
            qty="0.001",
            exec_time=int(exec_time.timestamp() * 1000),
        )

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        assert report.repairs_made == 1
        assert report.discrepancies[0].repaired is True
        mock_store.record_entry.assert_called_once_with(
            symbol="BTC/USDT",
            side="Buy",
            amount=Decimal("0.001"),
            entry_price=Decimal("64000.00"),
            regime="RECONCILED",
        )

    @pytest.mark.asyncio
    async def test_max_repairs_guard(self, reconciler, mock_bybit, mock_store):
        """If more than MAX_REPAIRS_PER_CYCLE missing, skip auto-repair."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=2)
        fills = [
            _make_bybit_fill(
                symbol=f"SYM{i}USDT",
                order_id=f"order{i}",
                exec_time=int(exec_time.timestamp() * 1000),
            )
            for i in range(11)
        ]
        # Add symbol mappings
        for i in range(11):
            reconciler.client._symbol_map[f"SYM{i}/USDT"] = f"SYM{i}USDT"

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": fills, "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 11
        assert report.repairs_made == 10  # Capped at MAX_REPAIRS_PER_CYCLE
        assert mock_store.record_entry.call_count == 10


# ── Unit: missing exit ───────────────────────────────────────


class TestMissingExit:
    @pytest.mark.asyncio
    async def test_detects_missing_exit(self, reconciler, mock_bybit, mock_store):
        """Bybit closed PnL with no local exit — missing_exit (CRITICAL)."""
        now = datetime.now(timezone.utc)
        entry_time = now - timedelta(hours=3)
        local = _make_local_trade(entry_time=entry_time, exit_time=None)  # Still open
        closed_pnl = {
            "symbol": "BTCUSDT",
            "side": "Sell",
            "closedPnl": "5.00",
            "avgEntryPrice": "64000",
            "avgExitPrice": "69000",
            "qty": "0.001",
            "createdTime": str(int((now - timedelta(minutes=30)).timestamp() * 1000)),
        }

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [closed_pnl], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].kind == "missing_exit"
        assert report.discrepancies[0].severity == "CRITICAL"

    @pytest.mark.asyncio
    async def test_sends_alert_on_critical(
        self, reconciler, mock_bybit, mock_store, mock_alert
    ):
        """CRITICAL discrepancy triggers Telegram alert."""
        now = datetime.now(timezone.utc)
        entry_time = now - timedelta(hours=3)
        local = _make_local_trade(entry_time=entry_time, exit_time=None)
        closed_pnl = {
            "symbol": "BTCUSDT",
            "side": "Sell",
            "closedPnl": "5.00",
            "avgEntryPrice": "64000",
            "avgExitPrice": "69000",
            "qty": "0.001",
            "createdTime": str(int((now - timedelta(minutes=30)).timestamp() * 1000)),
        }

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [closed_pnl], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])

        await reconciler.reconcile()

        mock_alert.send.assert_called_once()
        alert_text = mock_alert.send.call_args[0][0]
        assert "TRADE RECONCILIATION ALERT" in alert_text
        assert "missing_exit" in alert_text


# ── Unit: price mismatch ─────────────────────────────────────


class TestPriceMismatch:
    @pytest.mark.asyncio
    async def test_detects_price_mismatch(self, reconciler, mock_bybit, mock_store):
        """Bybit fill price differs >0.1% from local — price_mismatch."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=1)
        fill = _make_bybit_fill(
            price="65000.00", exec_time=int(exec_time.timestamp() * 1000)
        )
        local = _make_local_trade(entry_price=Decimal("64000.00"), entry_time=exec_time)

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].kind == "price_mismatch"
        assert report.discrepancies[0].severity == "WARNING"

    @pytest.mark.asyncio
    async def test_within_tolerance_no_mismatch(
        self, reconciler, mock_bybit, mock_store
    ):
        """Price within 0.1% tolerance — no mismatch."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=1)
        fill = _make_bybit_fill(
            price="64050.00", exec_time=int(exec_time.timestamp() * 1000)
        )
        local = _make_local_trade(entry_price=Decimal("64000.00"), entry_time=exec_time)

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 0


# ── Unit: grace period ───────────────────────────────────────


class TestGracePeriod:
    @pytest.mark.asyncio
    async def test_skips_recent_fills(self, reconciler, mock_bybit, mock_store):
        """Fills within grace period (5min) are skipped."""
        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(minutes=2)  # Within grace
        fill = _make_bybit_fill(exec_time=int(recent_time.timestamp() * 1000))

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        # Fill is skipped due to grace period, no discrepancy
        assert len(report.discrepancies) == 0


# ── Unit: multiple fills per order ───────────────────────────


class TestMultipleFills:
    @pytest.mark.asyncio
    async def test_aggregates_partial_fills(self, reconciler, mock_bybit, mock_store):
        """Multiple fills for same order aggregated before matching."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=2)
        ts = int(exec_time.timestamp() * 1000)
        fill1 = _make_bybit_fill(
            price="64000.00", qty="0.0005", order_id="order1", exec_time=ts
        )
        fill2 = _make_bybit_fill(
            price="64010.00", qty="0.0005", order_id="order1", exec_time=ts
        )
        # No local trade — should create one missing_entry with weighted avg price

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill1, fill2], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        report = await reconciler.reconcile()

        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].kind == "missing_entry"
        # Weighted avg: (64000*0.0005 + 64010*0.0005) / 0.001 = 64005
        assert Decimal(report.discrepancies[0].bybit_data["price"]) == Decimal(
            "64005.00"
        )


# ── Unit: error handling ─────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_bybit_api_error(self, reconciler, mock_bybit):
        """Bybit API error — report includes error, no crash."""
        mock_bybit.get_executions = AsyncMock(side_effect=Exception("API timeout"))

        report = await reconciler.reconcile()

        assert len(report.errors) == 1
        assert "API timeout" in report.errors[0]

    @pytest.mark.asyncio
    async def test_repair_failure_continues(self, reconciler, mock_bybit, mock_store):
        """Repair failure for one entry doesn't block others."""
        now = datetime.now(timezone.utc)
        exec_time = now - timedelta(hours=2)
        ts = int(exec_time.timestamp() * 1000)
        fill1 = _make_bybit_fill(order_id="o1", exec_time=ts)
        fill2 = _make_bybit_fill(order_id="o2", exec_time=ts)

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [fill1, fill2], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])
        mock_store.record_entry = AsyncMock(side_effect=[Exception("DB error"), None])

        report = await reconciler.reconcile()

        # Both discrepancies found, one repair failed, one succeeded
        assert len(report.discrepancies) == 2
        assert report.repairs_made == 1


# ── Unit: backfill ───────────────────────────────────────────


class TestBackfill:
    @pytest.mark.asyncio
    async def test_backfill_inserts_missing_trades(
        self, reconciler, mock_bybit, mock_store
    ):
        """Backfill inserts trades for Bybit records with no local match."""
        now = datetime.now(timezone.utc)
        ts = int((now - timedelta(hours=2)).timestamp() * 1000)
        closed_pnl = {
            "symbol": "BTCUSDT",
            "side": "Sell",
            "closedPnl": "5.00",
            "avgEntryPrice": "64000",
            "avgExitPrice": "69000",
            "qty": "0.001",
            "createdTime": str(ts),
            "updatedTime": str(ts + 60000),
        }

        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [closed_pnl], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])
        mock_store.record_full_trade = AsyncMock()

        inserted = await reconciler.backfill_from_bybit()

        assert inserted == 1
        mock_store.record_full_trade.assert_called_once()
        call_args = mock_store.record_full_trade.call_args
        assert call_args.kwargs["symbol"] == "BTC/USDT"
        assert call_args.kwargs["regime"] == "BACKFILL"
        assert reconciler._backfill_done is True

    @pytest.mark.asyncio
    async def test_backfill_skips_existing_trades(
        self, reconciler, mock_bybit, mock_store
    ):
        """Backfill skips Bybit records that already exist locally."""
        now = datetime.now(timezone.utc)
        entry_time = now - timedelta(hours=2)
        ts = int(entry_time.timestamp() * 1000)
        closed_pnl = {
            "symbol": "BTCUSDT",
            "side": "Sell",
            "closedPnl": "5.00",
            "avgEntryPrice": "64000",
            "avgExitPrice": "69000",
            "qty": "0.001",
            "createdTime": str(ts),
            "updatedTime": str(ts + 60000),
        }
        local = _make_local_trade(entry_time=entry_time)

        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [closed_pnl], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])
        mock_store.record_full_trade = AsyncMock()

        inserted = await reconciler.backfill_from_bybit()

        assert inserted == 0
        mock_store.record_full_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_runs_only_once(self, reconciler, mock_bybit, mock_store):
        """Second call to backfill is a no-op."""
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[])

        await reconciler.backfill_from_bybit()
        inserted = await reconciler.backfill_from_bybit()

        assert inserted == 0


# ── Unit: missing exit auto-repair ───────────────────────────


class TestMissingExitRepair:
    @pytest.mark.asyncio
    async def test_auto_repairs_missing_exit(self, reconciler, mock_bybit, mock_store):
        """Missing exit gets auto-repaired with record_full_trade."""
        now = datetime.now(timezone.utc)
        entry_time = now - timedelta(hours=3)
        local = _make_local_trade(entry_time=entry_time, exit_time=None)
        closed_pnl = {
            "symbol": "BTCUSDT",
            "side": "Sell",
            "closedPnl": "5.00",
            "avgEntryPrice": "64000",
            "avgExitPrice": "69000",
            "qty": "0.001",
            "createdTime": str(int((now - timedelta(minutes=30)).timestamp() * 1000)),
        }

        mock_bybit.get_executions = AsyncMock(
            return_value={"executions": [], "cursor": None}
        )
        mock_bybit.get_closed_pnl = AsyncMock(
            return_value={"closed_pnl": [closed_pnl], "cursor": None}
        )
        mock_store.get_trades_since = AsyncMock(return_value=[local])
        mock_store.close_trade = AsyncMock()

        report = await reconciler.reconcile()

        # missing_exit with local_trade_id should close existing trade, not insert duplicate
        assert report.repairs_made == 1
        assert report.discrepancies[0].repaired is True
        mock_store.close_trade.assert_called_once()
        call_args = mock_store.close_trade.call_args
        assert call_args.kwargs["exit_reason"] == "bybit_reconciled"
        assert call_args.kwargs["trade_id"] == local["id"]
