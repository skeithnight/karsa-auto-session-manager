"""Tests for app.backtest.orchestrator and app.backtest.formatter."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.backtest.formatter import (
    compute_backtest_summary,
    format_backtest_list,
    format_backtest_status,
    format_backtest_summary,
)
from app.backtest.orchestrator import (
    QUEUE_KEY,
    TELEMETRY_PREFIX,
    BacktestJobSpec,
    BacktestJobStatus,
    BacktestOrchestrator,
    BacktestTradeResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


NOW = datetime.now(UTC)


def _make_result(  # noqa: PLR0913
    symbol: str = "BTC/USDT",
    direction: str = "LONG",
    pnl_net: float = 10.0,
    regime: str = "TREND_BULL",
    exit_reason: str = "tp_hit",
    bars_held: int = 24,
    trade_taken: bool = True,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        job_id="job-123",
        symbol=symbol,
        direction=direction,
        regime=regime,
        score=72.5,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        exit_reason=exit_reason,
        sl_price=Decimal("49000"),
        tp_price=Decimal("52000"),
        amount=Decimal("0.01"),
        size_multiplier=Decimal("1"),
        pnl_gross=Decimal(str(pnl_net)),
        pnl_net=Decimal(str(pnl_net)),
        total_fees=Decimal("2.5"),
        total_funding=Decimal("0.5"),
        bars_held=bars_held,
        entry_time=NOW,
        exit_time=NOW,
        trade_taken=trade_taken,
    )


def _make_status(
    job_id: str = "job-123",
    status: str = "completed",
    symbol: str = "BTC/USDT",
    trades_taken: int = 5,
    total_pnl: str = "42.50",
) -> BacktestJobStatus:
    return BacktestJobStatus(
        job_id=job_id,
        status=status,
        symbol=symbol,
        trades_taken=trades_taken,
        total_pnl=total_pnl,
        submitted_at="2024-01-15T10:00:00Z",
        completed_at="2024-01-15T10:00:30Z",
    )


# ---------------------------------------------------------------------------
# BacktestJobSpec
# ---------------------------------------------------------------------------


class TestBacktestJobSpec:
    def test_defaults(self) -> None:
        spec = BacktestJobSpec(symbol="BTC/USDT")
        assert spec.symbol == "BTC/USDT"
        assert spec.candle_limit == 500
        assert spec.start_time is None
        assert spec.end_time is None

    def test_with_dates(self) -> None:
        spec = BacktestJobSpec(
            symbol="ETH/USDT",
            candle_limit=1000,
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-06-01T00:00:00Z",
        )
        assert spec.symbol == "ETH/USDT"
        assert spec.candle_limit == 1000
        assert spec.start_time is not None
        assert spec.end_time is not None


# ---------------------------------------------------------------------------
# BacktestJobStatus
# ---------------------------------------------------------------------------


class TestBacktestJobStatus:
    def test_defaults(self) -> None:
        s = BacktestJobStatus(job_id="j1", status="pending", symbol="BTC/USDT")
        assert s.trades_taken == 0
        assert s.error == ""


# ---------------------------------------------------------------------------
# BacktestOrchestrator
# ---------------------------------------------------------------------------


class TestBacktestOrchestrator:
    def _make_orch(self) -> tuple[BacktestOrchestrator, MagicMock, MagicMock]:
        redis = MagicMock()
        redis.redis = MagicMock()
        redis.redis.setex = AsyncMock()
        redis.redis.rpush = AsyncMock()
        redis.redis.get = AsyncMock(return_value=None)
        db = MagicMock()
        db.engine = MagicMock()
        orch = BacktestOrchestrator(redis, db)
        return orch, redis, db

    @pytest.mark.asyncio
    async def test_submit_job_pushes_to_redis(self) -> None:
        orch, redis, _ = self._make_orch()
        spec = BacktestJobSpec(symbol="BTC/USDT", candle_limit=500)

        job_id = await orch.submit_job(spec)

        assert len(job_id) == 36  # UUID
        redis.redis.setex.assert_called_once()
        redis.redis.rpush.assert_called_once()
        push_args = redis.redis.rpush.call_args
        assert push_args[0][0] == QUEUE_KEY

    @pytest.mark.asyncio
    async def test_submit_job_with_dates(self) -> None:
        orch, redis, _ = self._make_orch()
        spec = BacktestJobSpec(
            symbol="ETH/USDT",
            candle_limit=1000,
            start_time="2024-01-01",
            end_time="2024-06-01",
        )

        job_id = await orch.submit_job(spec)
        assert job_id

        push_args = redis.redis.rpush.call_args
        import json
        payload = json.loads(push_args[0][1])
        assert payload["symbol"] == "ETH/USDT"
        assert payload["candle_limit"] == 1000
        assert payload["start_time"] == "2024-01-01"
        assert payload["end_time"] == "2024-06-01"

    @pytest.mark.asyncio
    async def test_get_job_status_from_telemetry(self) -> None:
        orch, redis, _ = self._make_orch()
        import json
        redis.redis.get = AsyncMock(
            return_value=json.dumps({
                "status": "completed",
                "symbol": "BTC/USDT",
                "trades_taken": 5,
                "total_pnl": "42.5",
            })
        )

        status = await orch.get_job_status("job-123")
        assert status.status == "completed"
        assert status.trades_taken == 5
        assert status.total_pnl == "42.5"

    @pytest.mark.asyncio
    async def test_get_job_status_unknown_when_no_telemetry(self) -> None:
        orch, redis, _ = self._make_orch()
        redis.redis.get = AsyncMock(return_value=None)

        status = await orch.get_job_status("unknown-job")
        assert status.status == "unknown"

    @pytest.mark.asyncio
    async def test_update_job_telemetry(self) -> None:
        orch, redis, _ = self._make_orch()

        await orch.update_job_telemetry(
            job_id="job-456",
            status="completed",
            symbol="ETH/USDT",
            trades_taken=3,
            total_pnl="15.00",
        )

        redis.redis.setex.assert_called_once()
        call_args = redis.redis.setex.call_args
        assert call_args[0][0] == f"{TELEMETRY_PREFIX}job-456"


# ---------------------------------------------------------------------------
# compute_backtest_summary
# ---------------------------------------------------------------------------


class TestComputeBacktestSummary:
    def test_empty(self) -> None:
        s = compute_backtest_summary([])
        assert s.total_reports == 0
        assert s.trades_taken == 0

    def test_all_skipped(self) -> None:
        results = [_make_result(trade_taken=False) for _ in range(5)]
        s = compute_backtest_summary(results)
        assert s.total_reports == 5
        assert s.trades_taken == 0
        assert s.trades_skipped == 5

    def test_mixed_winners_losers(self) -> None:
        results = [
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=-5.0),
        ]
        s = compute_backtest_summary(results)
        assert s.trades_taken == 3
        assert s.winning_trades == 2
        assert s.losing_trades == 1
        assert s.win_rate == pytest.approx(200 / 3)
        assert s.net_pnl == Decimal("15")
        assert s.profit_factor == pytest.approx(4.0)

    def test_regime_counts(self) -> None:
        results = [
            _make_result(regime="TREND_BULL"),
            _make_result(regime="TREND_BULL"),
            _make_result(regime="RANGE"),
        ]
        s = compute_backtest_summary(results)
        assert s.regime_counts == {"TREND_BULL": 2, "RANGE": 1}

    def test_direction_counts(self) -> None:
        results = [
            _make_result(direction="LONG"),
            _make_result(direction="SHORT"),
            _make_result(direction="LONG"),
        ]
        s = compute_backtest_summary(results)
        assert s.direction_counts == {"LONG": 2, "SHORT": 1}

    def test_exit_reason_counts(self) -> None:
        results = [
            _make_result(exit_reason="tp_hit"),
            _make_result(exit_reason="tp_hit"),
            _make_result(exit_reason="sl_hit"),
        ]
        s = compute_backtest_summary(results)
        assert s.exit_reason_counts == {"tp_hit": 2, "sl_hit": 1}

    def test_avg_bars_held(self) -> None:
        results = [
            _make_result(bars_held=10),
            _make_result(bars_held=20),
            _make_result(bars_held=30),
        ]
        s = compute_backtest_summary(results)
        assert s.avg_bars_held == 20.0

    def test_all_winners_profit_factor_inf(self) -> None:
        results = [_make_result(pnl_net=10.0) for _ in range(3)]
        s = compute_backtest_summary(results)
        assert s.profit_factor == float("inf")


# ---------------------------------------------------------------------------
# format_backtest_status
# ---------------------------------------------------------------------------


class TestFormatBacktestStatus:
    def test_completed(self) -> None:
        status = _make_status(status="completed")
        text = format_backtest_status(status)
        assert "COMPLETED" in text
        assert "BTC/USDT" in text

    def test_failed(self) -> None:
        status = _make_status(status="failed", total_pnl="0")
        text = format_backtest_status(status)
        assert "FAILED" in text

    def test_pending(self) -> None:
        status = _make_status(status="pending", trades_taken=0, total_pnl="0")
        text = format_backtest_status(status)
        assert "PENDING" in text

    def test_with_error(self) -> None:
        status = _make_status(status="failed", total_pnl="0")
        status.error = "insufficient candles"
        text = format_backtest_status(status)
        assert "insufficient candles" in text


# ---------------------------------------------------------------------------
# format_backtest_summary
# ---------------------------------------------------------------------------


class TestFormatBacktestSummary:
    def test_empty_results(self) -> None:
        text = format_backtest_summary([], "job-abc")
        assert "Reports: 0" in text

    def test_with_results(self) -> None:
        results = [
            _make_result(pnl_net=10.0),
            _make_result(pnl_net=-5.0),
        ]
        text = format_backtest_summary(results, "job-abc")
        assert "Summary" in text
        assert "Win Rate" in text
        assert "Regime Breakdown" in text
        assert "Equity:" in text

    def test_with_status_symbol(self) -> None:
        results = [_make_result()]
        status = _make_status(symbol="BTC/USDT")
        text = format_backtest_summary(results, "job-abc", status)
        assert "Symbol: BTC/USDT" in text

    def test_recent_trades_shown(self) -> None:
        results = [_make_result(pnl_net=float(i)) for i in range(8)]
        text = format_backtest_summary(results, "job-abc")
        assert "Recent Trades" in text


# ---------------------------------------------------------------------------
# format_backtest_list
# ---------------------------------------------------------------------------


class TestFormatBacktestList:
    def test_empty(self) -> None:
        text = format_backtest_list([])
        assert "No backtest jobs" in text

    def test_with_jobs(self) -> None:
        jobs = [_make_status(), _make_status(job_id="job-456", symbol="ETH/USDT")]
        text = format_backtest_list(jobs)
        assert "Recent Backtest Jobs" in text
        assert "BTC/USDT" in text
        assert "ETH/USDT" in text
