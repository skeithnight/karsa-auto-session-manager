"""Shared test fixtures."""

from decimal import Decimal

import pytest


@pytest.fixture
def sample_orderbook() -> dict:
    """Sample L2 orderbook data for testing."""
    return {
        "symbol": "BTC/USDT:USDT",
        "bids": [[Decimal("64000.00"), Decimal("1.5")], [Decimal("63999.00"), Decimal("2.0")]],
        "asks": [[Decimal("64001.00"), Decimal("1.0")], [Decimal("64002.00"), Decimal("3.0")]],
        "timestamp": "2024-01-15T14:30:00Z",
        "exchange": "binance",
    }


@pytest.fixture
def sample_bad_tick() -> dict:
    """Sample bad tick (price spike >5%) for testing."""
    return {
        "symbol": "BTC/USDT:USDT",
        "price": Decimal("67200.00"),  # >5% spike from 64000
        "timestamp": "2024-01-15T14:30:01Z",
        "exchange": "binance",
    }
