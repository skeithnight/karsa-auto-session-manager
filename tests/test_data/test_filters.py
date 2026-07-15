"""Tests for Bad Tick Filter."""

from datetime import datetime, timezone
from decimal import Decimal


from app.data.filters import BadTickFilter
from app.data.normalizer import ExchangeData


class TestBadTickFilter:
    """Test suite for BadTickFilter class."""

    def setup_method(self) -> None:
        self.filter = BadTickFilter()

    def test_first_tick_accepted(self) -> None:
        """Test that the first tick is always accepted."""
        data = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64000"),
            timestamp=datetime.now(timezone.utc),
        )

        assert self.filter.is_bad_tick(data) is False

    def test_normal_price_change_accepted(self) -> None:
        """Test that normal price changes are accepted."""
        # First tick
        data1 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64000"),
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        )
        self.filter.is_bad_tick(data1)

        # Second tick — normal change (0.5% in 2 seconds)
        data2 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64320"),  # +0.5%
            timestamp=datetime(2024, 1, 15, 14, 30, 2, tzinfo=timezone.utc),
        )

        assert self.filter.is_bad_tick(data2) is False

    def test_bad_tick_rejected(self) -> None:
        """Test that bad ticks (>5% in <1s) are rejected."""
        # First tick
        data1 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64000"),
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        )
        self.filter.is_bad_tick(data1)

        # Second tick — bad tick (10% in 0.5 seconds)
        data2 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("70400"),  # +10%
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),  # Same second
        )

        assert self.filter.is_bad_tick(data2) is True

    def test_large_change_over_time_accepted(self) -> None:
        """Test that large changes over longer time are accepted."""
        # First tick
        data1 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64000"),
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        )
        self.filter.is_bad_tick(data1)

        # Second tick — 10% change over 5 seconds (allowed)
        data2 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("70400"),  # +10%
            timestamp=datetime(2024, 1, 15, 14, 30, 5, tzinfo=timezone.utc),  # 5 seconds later
        )

        assert self.filter.is_bad_tick(data2) is False

    def test_filter_orderbook_marks_stale(self) -> None:
        """Test that filter_orderbook marks bad ticks as stale."""
        # First tick
        data1 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("64000"),
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        )
        self.filter.filter_orderbook(data1)

        # Second tick — bad tick
        data2 = ExchangeData(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            last_price=Decimal("70400"),
            timestamp=datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc),
        )

        result = self.filter.filter_orderbook(data2)
        assert result.is_stale is True
