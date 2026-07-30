"""Tests for DailySummaryService — daily summary generation and formatting.

Tests cover:
- Summary message format and structure
- PnL calculation (today, week, month, total)
- Win rate calculation
- AI performance section
- Guardrail stats section
- Top/worst performers ranking
- Empty data handling
- Alert preference toggle
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.daily_summary import DailySummaryService


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Mock Redis client with common getters."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def mock_db_engine():
    """Mock DatabaseEngine with connect context manager."""
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    engine.engine = MagicMock()
    engine.engine.connect = MagicMock(return_value=conn)
    return engine, conn


@pytest.fixture
def service(mock_redis, mock_db_engine):
    """DailySummaryService with mocked dependencies."""
    engine, _ = mock_db_engine
    return DailySummaryService(redis_client=mock_redis, db_engine=engine)


# ── Alert Preference ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_enabled_default(service, mock_redis):
    """Default: daily summary is enabled (no Redis key set)."""
    mock_redis.get.return_value = None
    assert await service.is_enabled() is True


@pytest.mark.asyncio
async def test_is_enabled_explicit_on(service, mock_redis):
    """Explicitly enabled in Redis."""
    mock_redis.get.return_value = "1"
    assert await service.is_enabled() is True


@pytest.mark.asyncio
async def test_is_enabled_explicit_off(service, mock_redis):
    """Explicitly disabled in Redis."""
    mock_redis.get.return_value = "0"
    assert await service.is_enabled() is False


@pytest.mark.asyncio
async def test_is_enabled_bytes_on(service, mock_redis):
    """Redis returns bytes '1' — should be enabled."""
    mock_redis.get.return_value = b"1"
    assert await service.is_enabled() is True


@pytest.mark.asyncio
async def test_is_enabled_exception_fallback(service, mock_redis):
    """Redis error → fallback to enabled."""
    mock_redis.get.side_effect = Exception("redis down")
    assert await service.is_enabled() is True


# ── PnL Formatting ───────────────────────────────────────────────────────


def test_fmt_pnl_positive():
    """Positive PnL formatted with +$ prefix."""
    assert DailySummaryService._fmt_pnl(Decimal("125.50")) == "+$125.50"


def test_fmt_pnl_negative():
    """Negative PnL formatted with -$ prefix."""
    assert DailySummaryService._fmt_pnl(Decimal("-42.30")) == "-$42.30"


def test_fmt_pnl_zero():
    """Zero PnL formatted as +$0.00."""
    assert DailySummaryService._fmt_pnl(Decimal("0")) == "+$0.00"


def test_fmt_pnl_large():
    """Large PnL formatted with commas."""
    assert DailySummaryService._fmt_pnl(Decimal("1250.30")) == "+$1,250.30"


def test_fmt_pct_positive():
    """Positive percentage formatted with + prefix."""
    assert DailySummaryService._fmt_pct(12.5) == "+12.5%"


def test_fmt_pct_negative():
    """Negative percentage formatted with - prefix."""
    assert DailySummaryService._fmt_pct(-1.1) == "-1.1%"


def test_fmt_pct_zero():
    """Zero percentage formatted as +0.0%."""
    assert DailySummaryService._fmt_pct(0.0) == "+0.0%"


# ── Average Duration ──────────────────────────────────────────────────────


def test_compute_avg_duration_empty(service):
    """No trades -> N/A."""
    assert service._compute_avg_duration([]) == "N/A"


def test_compute_avg_duration_single_trade(service):
    """Single 3-hour trade -> 3h 0m."""
    now = datetime.now(timezone.utc)
    trades = [
        {
            "entry_time": now - timedelta(hours=3),
            "exit_time": now,
        }
    ]
    result = service._compute_avg_duration(trades)
    assert "3h" in result


def test_compute_avg_duration_minutes_only(service):
    """Sub-hour trade -> minutes only."""
    now = datetime.now(timezone.utc)
    trades = [
        {
            "entry_time": now - timedelta(minutes=45),
            "exit_time": now,
        }
    ]
    result = service._compute_avg_duration(trades)
    assert result == "45m"


def test_compute_avg_duration_string_timestamps(service):
    """Handles ISO string timestamps."""
    now = datetime.now(timezone.utc)
    trades = [
        {
            "entry_time": (now - timedelta(hours=2)).isoformat(),
            "exit_time": now.isoformat(),
        }
    ]
    result = service._compute_avg_duration(trades)
    assert "2h" in result


# ── Performers Ranking ────────────────────────────────────────────────────


def test_rank_performers_empty(service):
    """No trades -> empty lists."""
    top, worst = service._rank_performers([])
    assert top == []
    assert worst == []


def test_rank_performers_all_positive(service):
    """All winners -> top has entries, worst is empty."""
    trades = [
        {"symbol": "BTC/USDT", "pnl": 100, "entry_price": 50000, "amount": 0.1},
        {"symbol": "ETH/USDT", "pnl": 50, "entry_price": 3000, "amount": 1},
    ]
    top, worst = service._rank_performers(trades)
    assert len(top) == 2
    assert top[0]["symbol"] == "BTC/USDT"
    assert worst == []


def test_rank_performers_all_negative(service):
    """All losers -> worst has entries, top is empty."""
    trades = [
        {"symbol": "BTC/USDT", "pnl": -100, "entry_price": 50000, "amount": 0.1},
        {"symbol": "ETH/USDT", "pnl": -50, "entry_price": 3000, "amount": 1},
    ]
    top, worst = service._rank_performers(trades)
    assert top == []
    assert len(worst) == 2
    assert worst[0]["symbol"] == "BTC/USDT"  # most negative


def test_rank_performers_mixed(service):
    """Mixed results -> correct ranking."""
    trades = [
        {"symbol": "SOL/USDT", "pnl": 45.2, "entry_price": 100, "amount": 10},
        {"symbol": "ETH/USDT", "pnl": -22.3, "entry_price": 3000, "amount": 1},
        {"symbol": "AVAX/USDT", "pnl": 32.1, "entry_price": 30, "amount": 20},
    ]
    top, worst = service._rank_performers(trades)
    assert len(top) == 2
    assert top[0]["symbol"] == "SOL/USDT"
    assert len(worst) == 1
    assert worst[0]["symbol"] == "ETH/USDT"


# ── Message Formatting ────────────────────────────────────────────────────


def test_format_message_structure(service):
    """Message contains all required sections."""
    message = service._format_message(
        date_str="2025-01-15",
        today_pnl=Decimal("125.50"),
        today_pct=1.2,
        week_pnl=Decimal("450.20"),
        week_pct=4.5,
        month_pnl=Decimal("1250.30"),
        month_pct=12.5,
        total_pnl=Decimal("4520.50"),
        total_pct=45.2,
        signals_count=42,
        total_trades=8,
        win_count=5,
        win_rate=62.5,
        avg_duration="3h 45m",
        best_trade={"symbol": "SOL/USDT", "pnl": 45.2},
        ai_stats={
            "evaluations": 42,
            "avg_confidence": 76,
            "high_conf_wr": 72.0,
            "contribution": Decimal("85.30"),
            "cost": 0.65,
        },
        guardrail_stats={
            "hard_triggered": 5,
            "trades_blocked": 3,
            "estimated_savings": Decimal("-210"),
            "soft_triggered": 12,
            "downgrades": 8,
        },
        top_performers=[
            {"symbol": "SOL/USDT", "pnl": 45.2, "pct": 3.2},
            {"symbol": "AVAX/USDT", "pnl": 32.1, "pct": 2.8},
            {"symbol": "LINK/USDT", "pnl": 18.5, "pct": 1.9},
        ],
        worst_performers=[
            {"symbol": "ETH/USDT", "pnl": -22.3, "pct": -1.1},
            {"symbol": "DOT/USDT", "pnl": -12.5, "pct": -0.8},
        ],
    )

    # Verify key sections exist
    assert "Daily Summary" in message
    assert "PnL" in message
    assert "Trading Activity" in message
    assert "AI Performance" in message
    assert "Guardrails" in message
    assert "Top Performers" in message
    assert "Worst Performers" in message

    # Verify values
    assert "+$125.50" in message
    assert "+1.2%" in message
    assert "62.5%" in message
    assert "SOL/USDT" in message
    assert "ETH/USDT" in message


def test_format_message_no_trades(service):
    """Message handles zero trades gracefully."""
    message = service._format_message(
        date_str="2025-01-15",
        today_pnl=Decimal("0"),
        today_pct=0.0,
        week_pnl=Decimal("0"),
        week_pct=0.0,
        month_pnl=Decimal("0"),
        month_pct=0.0,
        total_pnl=Decimal("0"),
        total_pct=0.0,
        signals_count=0,
        total_trades=0,
        win_count=0,
        win_rate=0.0,
        avg_duration="N/A",
        best_trade=None,
        ai_stats={
            "evaluations": 0,
            "avg_confidence": 0,
            "high_conf_wr": 0.0,
            "contribution": Decimal("0"),
            "cost": 0.0,
        },
        guardrail_stats={
            "hard_triggered": 0,
            "trades_blocked": 0,
            "estimated_savings": Decimal("0"),
            "soft_triggered": 0,
            "downgrades": 0,
        },
        top_performers=[],
        worst_performers=[],
    )

    assert "N/A" in message
    assert "Best Trade: N/A" in message


def test_format_message_negative_pnl(service):
    """Negative PnL shows correct formatting."""
    message = service._format_message(
        date_str="2025-01-15",
        today_pnl=Decimal("-50.25"),
        today_pct=-2.1,
        week_pnl=Decimal("-120.00"),
        week_pct=-5.0,
        month_pnl=Decimal("-500.00"),
        month_pct=-15.0,
        total_pnl=Decimal("-1000.00"),
        total_pct=-30.0,
        signals_count=10,
        total_trades=5,
        win_count=1,
        win_rate=20.0,
        avg_duration="1h 30m",
        best_trade={"symbol": "BTC/USDT", "pnl": 10.0},
        ai_stats={
            "evaluations": 10,
            "avg_confidence": 65,
            "high_conf_wr": 40.0,
            "contribution": Decimal("-30.00"),
            "cost": 0.30,
        },
        guardrail_stats={
            "hard_triggered": 2,
            "trades_blocked": 1,
            "estimated_savings": Decimal("-50"),
            "soft_triggered": 5,
            "downgrades": 3,
        },
        top_performers=[],
        worst_performers=[
            {"symbol": "ETH/USDT", "pnl": -50.25, "pct": -2.1},
        ],
    )

    assert "-$50.25" in message
    assert "-2.1%" in message
    assert "-$120.00" in message


# ── Data Fetching (with mocked DB) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_trades_between_empty(service, mock_db_engine):
    """No trades in range → empty list."""
    _, conn = mock_db_engine
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    conn.execute.return_value = mock_result

    result = await service._fetch_trades_between(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 16, tzinfo=timezone.utc),
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_trades_between_exception(service, mock_db_engine):
    """DB error → empty list (graceful fallback)."""
    _, conn = mock_db_engine
    conn.execute.side_effect = Exception("db error")

    result = await service._fetch_trades_between(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 16, tzinfo=timezone.utc),
    )
    assert result == []


@pytest.mark.asyncio
async def test_count_signals_empty(service, mock_db_engine):
    """No signals → 0."""
    _, conn = mock_db_engine
    mock_result = MagicMock()
    mock_result.fetchone.return_value = [0]
    conn.execute.return_value = mock_result

    result = await service._count_signals(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 16, tzinfo=timezone.utc),
    )
    assert result == 0


@pytest.mark.asyncio
async def test_count_signals_exception(service, mock_db_engine):
    """DB error → 0 (graceful fallback)."""
    _, conn = mock_db_engine
    conn.execute.side_effect = Exception("db error")

    result = await service._count_signals(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 16, tzinfo=timezone.utc),
    )
    assert result == 0
