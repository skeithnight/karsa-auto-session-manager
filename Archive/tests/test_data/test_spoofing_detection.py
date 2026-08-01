"""Unit tests for spoofing detection in MarketDataIngestor."""

import time
import pytest
from unittest.mock import AsyncMock

from app.data.market_data_ingestor import MarketDataIngestor


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.set = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_spoofing_detection_top_5_levels(mock_redis):
    """Verify that >$500k level in top 5 bids canceled < 3.0s flags spoofing."""
    ingestor = MarketDataIngestor(redis_client=mock_redis, symbols=["BTC/USDT"])
    
    mock_session = AsyncMock()
    ingestor._session = mock_session

    # Cycle 1: Large bid ($600k) appears at level 1
    # price 60000.0 * volume 10.0 = 600,000 > 500,000
    mock_session.fetch_order_book = AsyncMock(
        return_value={
            "bids": [[60000.0, 10.0], [59990.0, 1.0]],
            "asks": [[60010.0, 1.0], [60020.0, 1.0]],
        }
    )

    await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT:USDT")
    assert ingestor.spoofing_bid.get("BTC/USDT", False) is False

    # Cycle 2: 1.0s later, the large bid vanishes from top 5
    mock_session.fetch_order_book = AsyncMock(
        return_value={
            "bids": [[59990.0, 1.0]],
            "asks": [[60010.0, 1.0]],
        }
    )

    await ingestor._fetch_orderbook("BTC/USDT", "BTC/USDT:USDT")
    
    # Spoofing flag should now be True!
    assert ingestor.spoofing_bid.get("BTC/USDT") is True
    mock_redis.set.assert_any_call("karsa:market:BTC/USDT:spoofing_bid", "true")

