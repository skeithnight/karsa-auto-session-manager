"""Tests for app.data.universe_scanner — Dynamic Universe Scanner."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.universe_scanner import (
    ATR_PERIOD,
    DEFAULT_TOP_N,
    REDIS_SCANNER_STATUS_KEY,
    REDIS_UNIVERSE_KEY,
    DynamicUniverseScanner,
    compute_atr,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scanner(**kwargs) -> tuple[DynamicUniverseScanner, MagicMock]:
    redis = MagicMock()
    redis.set = AsyncMock()
    defaults = {
        "redis_client": redis,
        "top_n": DEFAULT_TOP_N,
        "min_volume_usd": 5_000_000.0,
        "fallback_symbols": ["BTC/USDT", "ETH/USDT"],
    }
    defaults.update(kwargs)
    scanner = DynamicUniverseScanner(**defaults)
    return scanner, redis


def _sample_candles(n: int = ATR_PERIOD + 5) -> list:
    """Generate n sample OHLCV candles."""
    candles = []
    for i in range(n):
        ts = 1700000000000 + i * 3600000
        candles.append([ts, 100.0, 105.0, 95.0, 102.0, 1000.0])
    return candles


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeATR:
    def test_insufficient_data(self) -> None:
        assert compute_atr([100.0, 105.0], [90.0, 92.0], [95.0, 100.0]) == 0.0

    def test_exact_minimum_data(self) -> None:
        n = ATR_PERIOD + 1
        highs = [100.0 + i for i in range(n)]
        lows = [90.0 + i for i in range(n)]
        closes = [95.0 + i for i in range(n)]
        assert compute_atr(highs, lows, closes) > 0

    def test_constant_prices(self) -> None:
        n = ATR_PERIOD + 2
        assert compute_atr([100.0] * n, [100.0] * n, [100.0] * n) == 0.0

    def test_sample_data(self) -> None:
        candles = _sample_candles()
        assert compute_atr(
            [c[2] for c in candles], [c[3] for c in candles], [c[4] for c in candles],
        ) > 0


# ---------------------------------------------------------------------------
# DynamicUniverseScanner
# ---------------------------------------------------------------------------


class TestDynamicUniverseScanner:
    def test_defaults(self) -> None:
        scanner, _ = _make_scanner()
        assert scanner._top_n == DEFAULT_TOP_N
        assert scanner.symbols == []

    def test_get_active_symbols_empty(self) -> None:
        scanner, _ = _make_scanner()
        assert scanner.get_active_symbols() == []

    def test_get_active_symbols_returns_copy(self) -> None:
        scanner, _ = _make_scanner()
        scanner.symbols = ["BTC/USDT", "ETH/USDT"]
        symbols = scanner.get_active_symbols()
        assert symbols == ["BTC/USDT", "ETH/USDT"]
        assert symbols is not scanner.symbols

    @pytest.mark.asyncio
    async def test_refresh_no_session_uses_cache(self) -> None:
        scanner, _ = _make_scanner()
        scanner.symbols = ["BTC/USDT"]
        assert await scanner.refresh() == ["BTC/USDT"]

    @pytest.mark.asyncio
    async def test_refresh_fetches_tickers(self) -> None:
        scanner, _ = _make_scanner()
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0, "last": 50000.0},
            "ETH/USDT:USDT": {"quoteVolume": 50_000_000.0, "last": 3000.0},
        })
        scanner._session.markets = {
            "BTC/USDT:USDT": {"swap": True},
            "ETH/USDT:USDT": {"swap": True},
        }
        scanner._session.fetch_ohlcv = AsyncMock(return_value=_sample_candles())

        result = await scanner.refresh()
        assert "BTC/USDT" in result
        assert "ETH/USDT" in result

    @pytest.mark.asyncio
    async def test_refresh_filters_by_volume(self) -> None:
        scanner, _ = _make_scanner(min_volume_usd=10_000_000)
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0},
            "LOW/USDT:USDT": {"quoteVolume": 1_000_000.0},
        })
        scanner._session.markets = {
            "BTC/USDT:USDT": {"swap": True},
            "LOW/USDT:USDT": {"swap": True},
        }
        scanner._session.fetch_ohlcv = AsyncMock(return_value=_sample_candles())

        result = await scanner.refresh()
        assert "BTC/USDT" in result
        assert "LOW/USDT" not in result

    @pytest.mark.asyncio
    async def test_refresh_non_swap_filtered(self) -> None:
        scanner, _ = _make_scanner()
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0},
            "SPOT/USDT:USDT": {"quoteVolume": 100_000_000.0},
        })
        scanner._session.markets = {
            "BTC/USDT:USDT": {"swap": True},
            "SPOT/USDT:USDT": {"swap": False},
        }
        scanner._session.fetch_ohlcv = AsyncMock(return_value=_sample_candles())

        result = await scanner.refresh()
        assert "BTC/USDT" in result
        assert "SPOT/USDT" not in result

    @pytest.mark.asyncio
    async def test_refresh_fallback_on_api_error(self) -> None:
        scanner, _ = _make_scanner(fallback_symbols=["SOL/USDT"])
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(side_effect=Exception("API error"))
        assert await scanner.refresh() == ["SOL/USDT"]

    @pytest.mark.asyncio
    async def test_refresh_fallback_when_empty(self) -> None:
        scanner, _ = _make_scanner(fallback_symbols=["SOL/USDT"])
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(return_value={})
        assert await scanner.refresh() == ["SOL/USDT"]

    @pytest.mark.asyncio
    async def test_refresh_writes_redis(self) -> None:
        scanner, redis = _make_scanner()
        scanner._session = MagicMock()
        scanner._session.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0},
        })
        scanner._session.markets = {"BTC/USDT:USDT": {"swap": True}}
        scanner._session.fetch_ohlcv = AsyncMock(return_value=_sample_candles())

        await scanner.refresh()

        assert redis.set.call_count == 2
        keys = [c[0][0] for c in redis.set.call_args_list]
        assert REDIS_UNIVERSE_KEY in keys
        assert REDIS_SCANNER_STATUS_KEY in keys

    @pytest.mark.asyncio
    async def test_refresh_respects_top_n(self) -> None:
        scanner, _ = _make_scanner(top_n=2)
        scanner._session = MagicMock()
        tickers = {f"SYM{i}/USDT:USDT": {"quoteVolume": 100_000_000.0 - i * 1_000_000} for i in range(5)}
        scanner._session.fetch_tickers = AsyncMock(return_value=tickers)
        scanner._session.markets = {k: {"swap": True} for k in tickers}
        scanner._session.fetch_ohlcv = AsyncMock(return_value=_sample_candles())

        result = await scanner.refresh()
        assert len(result) <= 2

    def test_fallback_or_existing_uses_cache(self) -> None:
        scanner, _ = _make_scanner()
        scanner.symbols = ["BTC/USDT"]
        assert scanner._fallback_or_existing() == ["BTC/USDT"]

    def test_fallback_or_existing_uses_fallback(self) -> None:
        scanner, _ = _make_scanner(fallback_symbols=["SOL/USDT"])
        assert scanner._fallback_or_existing() == ["SOL/USDT"]

    @pytest.mark.asyncio
    async def test_stop_closes_session(self) -> None:
        scanner, _ = _make_scanner()
        mock = MagicMock()
        mock.close = AsyncMock()
        scanner._session = mock
        await scanner.stop()
        mock.close.assert_called_once()
        assert scanner._session is None
