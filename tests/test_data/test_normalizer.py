"""Tests for Data Normalizer."""

from decimal import Decimal

import pytest

from app.data.normalizer import Normalizer, ExchangeData, GlobalState


class TestNormalizer:
    """Test suite for Normalizer class."""

    def setup_method(self) -> None:
        self.normalizer = Normalizer()

    def test_normalize_orderbook_basic(self) -> None:
        """Test basic orderbook normalization."""
        raw_data = {
            "bids": [[64000.00, 1.5], [63999.00, 2.0]],
            "asks": [[64001.00, 1.0], [64002.00, 3.0]],
        }

        result = self.normalizer.normalize_orderbook(raw_data, "binance", "BTC/USDT:USDT")

        assert result.exchange == "binance"
        assert result.symbol == "BTC/USDT:USDT"
        assert len(result.bids) == 2
        assert len(result.asks) == 2
        assert result.bids[0] == (Decimal("64000.00"), Decimal("1.5"))
        assert result.asks[0] == (Decimal("64001.00"), Decimal("1.0"))

    def test_normalize_orderbook_empty(self) -> None:
        """Test orderbook normalization with empty data."""
        raw_data = {"bids": [], "asks": []}

        result = self.normalizer.normalize_orderbook(raw_data, "binance", "BTC/USDT:USDT")

        assert result.bids == []
        assert result.asks == []

    def test_normalize_orderbook_decimal_precision(self) -> None:
        """Test that Decimal precision is preserved."""
        raw_data = {
            "bids": [[64000.12345678, 1.12345678]],
            "asks": [[64001.87654321, 0.87654321]],
        }

        result = self.normalizer.normalize_orderbook(raw_data, "binance", "BTC/USDT:USDT")

        assert result.bids[0][0] == Decimal("64000.12345678")
        assert result.bids[0][1] == Decimal("1.12345678")

    def test_normalize_trade_basic(self) -> None:
        """Test basic trade normalization."""
        raw_trade = {"price": 64000.50, "amount": 0.5}

        result = self.normalizer.normalize_trade(raw_trade, "binance", "BTC/USDT:USDT")

        assert result.last_price == Decimal("64000.50")
        assert result.exchange == "binance"

    def test_build_global_state(self) -> None:
        """Test GlobalState aggregation."""
        exchanges = [
            ExchangeData(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                last_price=Decimal("64000"),
                timestamp="2024-01-15T14:30:00Z",
            ),
            ExchangeData(
                exchange="okx",
                symbol="BTC/USDT:USDT",
                last_price=Decimal("64001"),
                timestamp="2024-01-15T14:30:00Z",
            ),
        ]

        result = self.normalizer.build_global_state("BTC/USDT:USDT", exchanges)

        assert result.symbol == "BTC/USDT:USDT"
        assert len(result.exchanges) == 2

    def test_build_global_state_excludes_stale(self) -> None:
        """Test that stale exchanges are excluded from GlobalState."""
        exchanges = [
            ExchangeData(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                last_price=Decimal("64000"),
                timestamp="2024-01-15T14:30:00Z",
                is_stale=False,
            ),
            ExchangeData(
                exchange="okx",
                symbol="BTC/USDT:USDT",
                last_price=Decimal("64001"),
                timestamp="2024-01-15T14:30:00Z",
                is_stale=True,
            ),
        ]

        result = self.normalizer.build_global_state("BTC/USDT:USDT", exchanges)

        assert len(result.exchanges) == 1
        assert result.exchanges[0].exchange == "binance"
