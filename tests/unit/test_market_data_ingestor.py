"""Tests for app.data.market_data_ingestor — orderbook, funding, OI."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data.market_data_ingestor import REDIS_KEY_PREFIX, MarketDataIngestor


def _make_ingestor() -> tuple[MarketDataIngestor, MagicMock]:
    redis = MagicMock()
    redis.set = AsyncMock()
    ingestor = MarketDataIngestor(
        redis_client=redis,
        symbols=["BTC/USDT", "ETH/USDT"],
        poll_interval_s=9999,
    )
    ingestor._session = MagicMock()
    ingestor._session.fetch_order_book = AsyncMock()
    ingestor._session.fetch_funding_rate = AsyncMock()
    ingestor._session.fetch_open_interest = AsyncMock()
    return ingestor, redis


# ---------------------------------------------------------------------------
# Orderbook delta
# ---------------------------------------------------------------------------


class TestOrderbookDelta:
    @pytest.mark.asyncio
    async def test_positive_delta(self) -> None:
        """Bid vol > ask vol → positive delta."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {
            "bids": [[50000, 10], [49990, 5]],
            "asks": [[50010, 5], [50020, 3]],
        }
        await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT")
        assert ingestor.orderbook_delta["BTC/USDT"] > 0

    @pytest.mark.asyncio
    async def test_negative_delta(self) -> None:
        """Ask vol > bid vol → negative delta."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {
            "bids": [[50000, 3]], "asks": [[50010, 10]],
        }
        await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT")
        assert ingestor.orderbook_delta["BTC/USDT"] < 0

    @pytest.mark.asyncio
    async def test_equal_volume(self) -> None:
        """Equal bid/ask vol → delta ≈ 0."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {
            "bids": [[50000, 5]], "asks": [[50010, 5]],
        }
        await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT")
        assert abs(ingestor.orderbook_delta["BTC/USDT"]) < 0.01

    @pytest.mark.asyncio
    async def test_redis_published(self) -> None:
        """Redis key set on orderbook fetch."""
        ingestor, redis = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {
            "bids": [[50000, 10]], "asks": [[50010, 10]],
        }
        await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT")
        redis.set.assert_called()
        key = redis.set.call_args[0][0]
        assert "shadow:price:BTC/USDT" in key

    @pytest.mark.asyncio
    async def test_delta_range_clamped(self) -> None:
        """Delta always between -1 and 1."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {
            "bids": [[50000, 100]], "asks": [[50010, 0.001]],
        }
        await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT")
        assert -1.0 <= ingestor.orderbook_delta["BTC/USDT"] <= 1.0


# ---------------------------------------------------------------------------
# Funding rate
# ---------------------------------------------------------------------------


class TestFundingRate:
    @pytest.mark.asyncio
    async def test_positive_rate(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": 0.00015}
        await ingestor._fetch_funding_rate("BTC/USDT", "BTC/USDT")
        assert ingestor.funding_rate["BTC/USDT"] == 0.00015

    @pytest.mark.asyncio
    async def test_negative_rate(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": -0.00015}
        await ingestor._fetch_funding_rate("ETH/USDT", "ETH/USDT")
        assert ingestor.funding_rate["ETH/USDT"] == -0.00015

    @pytest.mark.asyncio
    async def test_zero_rate(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": 0.0}
        await ingestor._fetch_funding_rate("BTC/USDT", "BTC/USDT")
        assert ingestor.funding_rate["BTC/USDT"] == 0.0

    @pytest.mark.asyncio
    async def test_redis_published(self) -> None:
        ingestor, redis = _make_ingestor()
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": 0.0001}
        await ingestor._fetch_funding_rate("BTC/USDT", "BTC/USDT")
        redis.set.assert_called()
        key = redis.set.call_args[0][0]
        assert f"{REDIS_KEY_PREFIX}:BTC/USDT:funding_rate" in key


# ---------------------------------------------------------------------------
# Open interest change
# ---------------------------------------------------------------------------


class TestOIChange:
    @pytest.mark.asyncio
    async def test_positive_change(self) -> None:
        """OI increase → positive change."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 110.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        assert ingestor.oi_change["BTC/USDT"] > 0

    @pytest.mark.asyncio
    async def test_negative_change_capitulation(self) -> None:
        """OI decrease → negative change (capitulation signal)."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 80.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        assert ingestor.oi_change["BTC/USDT"] < 0

    @pytest.mark.asyncio
    async def test_unchanged(self) -> None:
        """Same OI → change ≈ 0."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        assert abs(ingestor.oi_change["BTC/USDT"]) < 0.001

    @pytest.mark.asyncio
    async def test_first_call_baseline(self) -> None:
        """First OI call → no change (baseline only)."""
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        assert ingestor.oi_change.get("BTC/USDT", 0) == 0.0

    @pytest.mark.asyncio
    async def test_redis_published(self) -> None:
        ingestor, redis = _make_ingestor()
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_oi("BTC/USDT", "BTC/USDT")
        redis.set.assert_called()
        key = redis.set.call_args[0][0]
        assert f"{REDIS_KEY_PREFIX}:BTC/USDT:oi_change" in key


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


class TestIntegrationHelpers:
    @pytest.mark.asyncio
    async def test_fetch_symbol_calls_all_three(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book.return_value = {"bids": [[1, 1]], "asks": [[2, 1]]}
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": 0.0}
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_symbol("BTC/USDT")
        assert "BTC/USDT" in ingestor.orderbook_delta
        assert "BTC/USDT" in ingestor.funding_rate

    @pytest.mark.asyncio
    async def test_fetch_symbol_orderbook_fails_gracefully(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor._session.fetch_order_book = AsyncMock(side_effect=Exception("API error"))
        ingestor._session.fetch_funding_rate.return_value = {"fundingRate": 0.0}
        ingestor._session.fetch_open_interest.return_value = {"openInterestAmount": 100.0}
        await ingestor._fetch_symbol("BTC/USDT")
        assert "BTC/USDT" in ingestor.oi_change

    def test_update_consumer(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor.orderbook_delta["BTC/USDT"] = 0.5
        ingestor.funding_rate["BTC/USDT"] = 0.0001
        ingestor.oi_change["BTC/USDT"] = -0.05
        consumer = MagicMock()
        consumer.orderbook_delta = {}
        consumer.funding_rate = {}
        consumer.oi_change = {}
        ingestor.update_consumer(consumer)
        assert consumer.orderbook_delta["BTC/USDT"] == 0.5
        assert consumer.funding_rate["BTC/USDT"] == 0.0001
        assert consumer.oi_change["BTC/USDT"] == -0.05

    def test_get_all(self) -> None:
        ingestor, _ = _make_ingestor()
        ingestor.orderbook_delta["BTC/USDT"] = 0.5
        ingestor.funding_rate["BTC/USDT"] = 0.0001
        ingestor.oi_change["BTC/USDT"] = -0.05
        data = ingestor.get_all("BTC/USDT")
        assert data["orderbook_delta"] == 0.5
        assert data["funding_rate"] == 0.0001
        assert data["oi_change"] == -0.05

    def test_get_all_missing(self) -> None:
        ingestor, _ = _make_ingestor()
        data = ingestor.get_all("UNKNOWN")
        assert data["orderbook_delta"] is None
        assert data["funding_rate"] is None
        assert data["oi_change"] is None

    @pytest.mark.asyncio
    async def test_stop_closes_session(self) -> None:
        ingestor, _ = _make_ingestor()
        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        ingestor._session = mock_session
        await ingestor.stop()
        mock_session.close.assert_called_once()
        assert ingestor._session is None
