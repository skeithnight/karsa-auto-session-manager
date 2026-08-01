"""Tests for ShadowExecutor component."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.redis_client import RedisClient
from app.execution.shadow import ShadowExecutor


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock(spec=RedisClient)
    return redis


@pytest.fixture
def shadow_executor(mock_redis):
    """Create ShadowExecutor with mocked dependencies."""
    mock_pos_store = AsyncMock()
    mock_trade_store = AsyncMock()

    # We patch get_settings to provide predictable shadow configs
    with patch("app.execution.shadow.get_settings") as mock_settings:
        settings_mock = MagicMock()
        settings_mock.shadow_slippage_pct = "0.0005"
        settings_mock.shadow_taker_fee_pct = "0.00055"
        settings_mock.shadow_maker_fee_pct = "0.0002"
        mock_settings.return_value = settings_mock

        return ShadowExecutor(
            redis_client=mock_redis,
            position_store=mock_pos_store,
            trade_store=mock_trade_store,
        )


class TestShadowExecutor:
    """Test suite for ShadowExecutor."""

    @pytest.mark.asyncio
    async def test_fee_asymmetry_maker_fee(self, shadow_executor, mock_redis):
        """Test is_post_only=True uses the maker fee (0.0002) and returns PENDING."""
        # Setup mid price
        mock_redis.get.return_value = json.dumps({"bid": "64000", "ask": "64000", "last": "64000"})

        result = await shadow_executor.execute(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("1.0"),
            is_post_only=True
        )

        assert result is not None
        assert result["status"] == "PENDING_VIRTUAL_FILL"
        assert result["fee_type"] == "maker"
        # 0.0002 maker fee. Fill will be 64000 * 1.0005 = 64032.0.
        # Fee: 64032.0 * 0.0002 = 12.8064 -> 12.81
        assert result["fee"] == "12.81"
        assert "pending_since" in result
        assert result["is_shadow"] is True

    @pytest.mark.asyncio
    async def test_fee_asymmetry_taker_fee(self, shadow_executor, mock_redis):
        """Test is_post_only=False uses the taker fee (0.00055) and returns filled."""
        mock_redis.get.return_value = json.dumps({"bid": "64000", "ask": "64000", "last": "64000"})

        result = await shadow_executor.execute(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("1.0"),
            is_post_only=False
        )

        assert result is not None
        assert result["status"] == "filled"
        assert result["fee_type"] == "taker"
        # 0.00055 taker fee. Fill will be 64000 * 1.0005 = 64032.0.
        # Fee: 64032.0 * 0.00055 = 35.2176 -> 35.22
        assert result["fee"] == "35.22"
        assert "pending_since" not in result

    @pytest.mark.asyncio
    async def test_slippage_math(self, shadow_executor, mock_redis):
        """Test slippage logic: worse execution by 0.05%."""
        mock_redis.get.return_value = json.dumps({"ask": "100000.0", "bid": "100000.0"})

        # Test buy: price * (1 + 0.0005) -> price * 1.0005
        buy_result = await shadow_executor.execute("BTC/USDT", "buy", Decimal("1.0"))
        assert buy_result["price"] == Decimal("100050.00000000")

        # Test sell: price * (1 - 0.0005) -> price * 0.9995
        sell_result = await shadow_executor.execute("BTC/USDT", "sell", Decimal("1.0"))
        assert sell_result["price"] == Decimal("99950.00000000")

    @pytest.mark.asyncio
    async def test_order_id_format(self, shadow_executor, mock_redis):
        """Test order IDs properly format SHADOW-<8char uuid> and increment counter."""
        mock_redis.get.return_value = json.dumps({"last": "64000"})

        r1 = await shadow_executor.execute("BTC/USDT", "buy", Decimal("1.0"))
        r2 = await shadow_executor.execute("ETH/USDT", "sell", Decimal("1.0"))

        assert r1["id"].startswith("SHADOW-")
        assert len(r1["id"]) == 7 + 8  # "SHADOW-" + 8 char hex
        assert r1["id"] != r2["id"]
        assert shadow_executor._counter == 2

    @pytest.mark.asyncio
    async def test_negative_or_zero_amount_rejection(self, shadow_executor, mock_redis):
        """negative or zero amounts return None immediately."""
        r1 = await shadow_executor.execute("BTC", "buy", Decimal("0.0"))
        r2 = await shadow_executor.execute("BTC", "sell", Decimal("-1.0"))
        r3 = await shadow_executor.execute_exit("BTC", "buy", Decimal("0.0"))

        assert r1 is None
        assert r2 is None
        assert r3 is None
        mock_redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_exit_always_uses_taker_fee(self, shadow_executor, mock_redis):
        """Exits always execute as Market Orders (Taker)."""
        mock_redis.get.return_value = json.dumps({"last": "50000"})

        result = await shadow_executor.execute_exit("BTC/USDT", "LONG", Decimal("1.0"))

        assert result is not None
        assert result["side"] == "sell"  # Closing a buy means selling
        assert result["status"] == "filled"
        assert result["reason"] == "manual"
        # 50000 * 0.9995 = 49975 (sell slippage)
        # fee = 49975 * 0.00055 = 27.48625 -> 27.49
        assert result["fee"] == "27.49"

    @pytest.mark.asyncio
    async def test_mid_price_lookup_fallback(self, shadow_executor, mock_redis):
        """Test get_mid_price fallback logic: system:state -> ticker"""
        # Scenario 1: First try succeeds with last price only
        mock_redis.get.side_effect = lambda key: json.dumps({"last": "12345"}) if key == "global:state:BTC/USDT" else None
        price = await shadow_executor._get_mid_price("BTC/USDT")
        assert price == Decimal("12345")

        # Scenario 2: System state empty, falls back to ticker
        mock_redis.get.side_effect = lambda key: json.dumps({"last": "54321"}) if key == "ticker:BTC/USDT" else None
        price2 = await shadow_executor._get_mid_price("BTC/USDT")
        assert price2 == Decimal("54321")

        # Scenario 3: Not found on both raises ValueError
        mock_redis.get.side_effect = lambda key: None
        with pytest.raises(ValueError, match="no price for BTC/USDT"):
            await shadow_executor._get_mid_price("BTC/USDT")
