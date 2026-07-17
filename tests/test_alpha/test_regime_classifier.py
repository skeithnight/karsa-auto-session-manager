"""Tests for Phase 6 RegimeClassifier — per-symbol regime detection."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier


class TestRegimeClassifierClassify:
    """Test classify() method — pure, no Redis needed."""

    def test_insufficient_data_returns_chop(self):
        rc = RegimeClassifier()
        candles = np.array([[0, 0, 101, 99, 100, 1000]] * 30, dtype=float)
        assert rc.classify(candles) == MarketRegime.CHOP

    def test_all_flat_returns_range(self):
        rc = RegimeClassifier()
        candles = np.array([[0, 100, 101, 99, 100, 1000]] * 60, dtype=float)
        assert rc.classify(candles) == MarketRegime.RANGE

    def test_empty_candles_returns_chop(self):
        rc = RegimeClassifier()
        candles = np.array([], dtype=float).reshape(0, 6)
        assert rc.classify(candles) == MarketRegime.CHOP

    def test_list_input_works(self):
        rc = RegimeClassifier()
        candles = [[0, 100, 101, 99, 100, 1000]] * 60
        regime = rc.classify(candles)
        assert regime in MarketRegime


class TestGetCurrentRegimePerSymbol:
    """Test get_current_regime() reads per-symbol key first, falls back to global."""

    def setup_method(self):
        self.rc = RegimeClassifier()

    @pytest.mark.asyncio
    async def test_per_symbol_key_used_when_present(self):
        """When per-symbol regime exists, use it — not the global one."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:ETH:USDT": "TREND_BULL",
                "system:config:regime": json.dumps({"regime": "CHOP"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("ETH/USDT")
        assert regime == MarketRegime.TREND_BULL
        mock_redis.get.assert_any_call("system:regime:ETH:USDT")

    @pytest.mark.asyncio
    async def test_fallback_to_global_when_no_per_symbol(self):
        """When per-symbol key missing, fall back to global BTC regime."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:SOL:USDT": None,
                "system:config:regime": json.dumps({"regime": "RANGE"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("SOL/USDT")
        assert regime == MarketRegime.RANGE

    @pytest.mark.asyncio
    async def test_fallback_to_chop_when_nothing_in_redis(self):
        """When nothing in Redis, return CHOP (conservative)."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_no_redis_returns_chop(self):
        """When no Redis client, return CHOP."""
        self.rc._redis = None
        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_redis_read_error_returns_chop(self):
        """When Redis read fails, return CHOP."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("connection lost"))
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("BTC/USDT")
        assert regime == MarketRegime.CHOP

    @pytest.mark.asyncio
    async def test_per_symbol_takes_priority_over_global(self):
        """Per-symbol CHOP should override global TREND_BULL."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            side_effect=lambda key: {
                "system:regime:DOGE:USDT": "CHOP",
                "system:config:regime": json.dumps({"regime": "TREND_BULL"}),
            }.get(key)
        )
        self.rc._redis = mock_redis

        regime = await self.rc.get_current_regime("DOGE/USDT")
        assert regime == MarketRegime.CHOP
