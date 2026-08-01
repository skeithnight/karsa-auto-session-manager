"""Sprint 2 Unit Tests — Liquidation Heatmap, Cross-Asset Momentum, Token Unlock, Half-Kelly Uncertainty."""

from __future__ import annotations

import json
import statistics
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.risk.kelly_sizer import KellySizer, MIN_RISK_PCT, MAX_RISK_PCT


# ─── Liquidation Heatmap Tests ────────────────────────────────────────

class TestLiquidationHeatmap:
    """Tests for Liquidation Heatmap signal evaluation."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def router(self):
        from app.alpha.strategy_router import StrategyRouter
        return StrategyRouter()

    @pytest.mark.asyncio
    async def test_no_redis_returns_zero(self, router):
        result = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", None)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_no_history_returns_zero(self, router, mock_redis):
        mock_redis.get.return_value = None
        result = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_insufficient_history_returns_zero(self, router, mock_redis):
        history = [{"oi_change": -0.06, "price": 100.0}]
        mock_redis.get.return_value = json.dumps(history).encode()
        result = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_no_liquidation_events_returns_zero(self, router, mock_redis):
        # OI changes are small (not liquidations)
        history = [
            {"oi_change": -0.01, "price": 100.0},
            {"oi_change": -0.02, "price": 101.0},
            {"oi_change": -0.03, "price": 102.0},
        ]
        mock_redis.get.side_effect = [
            json.dumps(history).encode(),  # OI history
            b"100.5",  # current price
        ]
        result = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_liquidation_cluster_detected(self, router, mock_redis):
        # Multiple OI drops > 5% near current price
        history = [
            {"oi_change": -0.06, "price": 100.0},
            {"oi_change": -0.08, "price": 100.5},
            {"oi_change": -0.07, "price": 101.0},
        ]
        mock_redis.get.side_effect = [
            json.dumps(history).encode(),  # OI history
            b"100.2",  # current price (within 2% of all events)
        ]
        bonus, reason = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis)
        assert bonus == 20
        assert "LIQ_HEATMAP" in reason

    @pytest.mark.asyncio
    async def test_liquidation_events_too_far_returns_zero(self, router, mock_redis):
        # OI drops exist but far from current price
        history = [
            {"oi_change": -0.06, "price": 90.0},  # 10% away
            {"oi_change": -0.08, "price": 91.0},
            {"oi_change": -0.07, "price": 92.0},
        ]
        mock_redis.get.side_effect = [
            json.dumps(history).encode(),
            b"100.0",  # current price
        ]
        bonus, reason = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis)
        assert bonus == 0

    @pytest.mark.asyncio
    async def test_custom_config_thresholds(self, router, mock_redis):
        # Use a real object with attributes instead of MagicMock
        class MockConfig:
            liq_heatmap_confluence_threshold = 2
            liq_heatmap_range_pct = "0.05"  # 5% range
            liq_heatmap_score_bonus = 25

        config = MockConfig()

        # Need 3+ entries to pass the len check
        history = [
            {"oi_change": -0.02, "price": 100.0},
            {"oi_change": -0.06, "price": 100.0},
            {"oi_change": -0.08, "price": 102.0},  # within 5% of 100
        ]
        mock_redis.get.side_effect = [
            json.dumps(history).encode(),
            b"100.0",
        ]
        bonus, reason = await router.evaluate_liquidation_heatmap("SOL/USDT", "LONG", mock_redis, config)
        assert bonus == 25


# ─── Cross-Asset Momentum Tests ───────────────────────────────────────

class TestCrossAssetMomentum:
    """Tests for Cross-Asset Momentum signal evaluation."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def router(self):
        from app.alpha.strategy_router import StrategyRouter
        return StrategyRouter()

    @pytest.mark.asyncio
    async def test_no_redis_returns_zero(self, router):
        result = await router.evaluate_cross_asset_momentum("SOL/USDT", "LONG", None)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_missing_btc_eth_returns_zero(self, router, mock_redis):
        mock_redis.get.return_value = None
        result = await router.evaluate_cross_asset_momentum("SOL/USDT", "LONG", mock_redis)
        assert result == (0, "")

    @pytest.mark.asyncio
    async def test_aligned_long_momentum(self, router, mock_redis):
        # All assets moving up — need 6+ entries for lookback=6
        btc_history = [{"price": 59000}, {"price": 59500}, {"price": 60000},
                       {"price": 60500}, {"price": 61000}, {"price": 61500}]
        eth_history = [{"price": 2900}, {"price": 2950}, {"price": 3000},
                       {"price": 3050}, {"price": 3100}, {"price": 3150}]
        alt_history = [{"price": 95}, {"price": 97}, {"price": 100},
                       {"price": 103}, {"price": 106}, {"price": 110}]

        mock_redis.get.side_effect = [
            json.dumps(btc_history).encode(),
            json.dumps(eth_history).encode(),
            json.dumps(alt_history).encode(),
        ]
        bonus, reason = await router.evaluate_cross_asset_momentum("SOL/USDT", "LONG", mock_redis)
        assert bonus == 15
        assert "CROSS_ASSET" in reason

    @pytest.mark.asyncio
    async def test_aligned_short_momentum(self, router, mock_redis):
        # All assets moving down — need 6+ entries for lookback=6
        btc_history = [{"price": 62000}, {"price": 61500}, {"price": 61000},
                       {"price": 60500}, {"price": 60000}, {"price": 59500}]
        eth_history = [{"price": 3200}, {"price": 3150}, {"price": 3100},
                       {"price": 3050}, {"price": 3000}, {"price": 2950}]
        alt_history = [{"price": 115}, {"price": 112}, {"price": 108},
                       {"price": 104}, {"price": 100}, {"price": 96}]

        mock_redis.get.side_effect = [
            json.dumps(btc_history).encode(),
            json.dumps(eth_history).encode(),
            json.dumps(alt_history).encode(),
        ]
        bonus, reason = await router.evaluate_cross_asset_momentum("SOL/USDT", "SHORT", mock_redis)
        assert bonus == 15
        assert "CROSS_ASSET" in reason

    @pytest.mark.asyncio
    async def test_mixed_momentum_returns_zero(self, router, mock_redis):
        # BTC up, ETH down — no alignment, need 6+ entries
        btc_history = [{"price": 59000}, {"price": 59500}, {"price": 60000},
                       {"price": 60500}, {"price": 61000}, {"price": 61500}]
        eth_history = [{"price": 3200}, {"price": 3150}, {"price": 3100},
                       {"price": 3050}, {"price": 3000}, {"price": 2950}]
        alt_history = [{"price": 95}, {"price": 97}, {"price": 100},
                       {"price": 103}, {"price": 106}, {"price": 110}]

        mock_redis.get.side_effect = [
            json.dumps(btc_history).encode(),
            json.dumps(eth_history).encode(),
            json.dumps(alt_history).encode(),
        ]
        bonus, reason = await router.evaluate_cross_asset_momentum("SOL/USDT", "LONG", mock_redis)
        assert bonus == 0

    @pytest.mark.asyncio
    async def test_weak_momentum_returns_zero(self, router, mock_redis):
        # Very small moves (< 0.5%), need 6+ entries
        btc_history = [{"price": 60000}, {"price": 60002}, {"price": 60004},
                       {"price": 60006}, {"price": 60008}, {"price": 60010}]
        eth_history = [{"price": 3000}, {"price": 3000.5}, {"price": 3001},
                       {"price": 3001.5}, {"price": 3002}, {"price": 3002.5}]
        alt_history = [{"price": 100}, {"price": 100.02}, {"price": 100.04},
                       {"price": 100.06}, {"price": 100.08}, {"price": 100.1}]

        mock_redis.get.side_effect = [
            json.dumps(btc_history).encode(),
            json.dumps(eth_history).encode(),
            json.dumps(alt_history).encode(),
        ]
        bonus, reason = await router.evaluate_cross_asset_momentum("SOL/USDT", "LONG", mock_redis)
        assert bonus == 0


# ─── Token Unlock Calendar Tests ──────────────────────────────────────

class TestTokenUnlockCalendar:
    """Tests for Token Unlock Calendar Filter."""

    @pytest.fixture
    def mock_redis(self):
        return AsyncMock()

    @pytest.fixture
    def engine(self):
        from app.consumer.decision_engine import DecisionEngine
        from app.alpha.market_analyzer import MarketAnalyzer
        from app.risk.dynamic_risk_gate import DynamicRiskGate

        analyzer = MagicMock(spec=MarketAnalyzer)
        risk_gate = MagicMock(spec=DynamicRiskGate)
        return DecisionEngine(
            analyzer=analyzer,
            router=MagicMock(),
            risk_gate=risk_gate,
            redis_client=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_no_unlock_returns_zero(self, engine, mock_redis):
        engine._redis = mock_redis
        mock_redis.get.return_value = None
        result = await engine._check_token_unlock("SOL/USDT", "LONG")
        assert result == 0

    @pytest.mark.asyncio
    async def test_unlock_below_threshold_returns_zero(self, engine, mock_redis):
        engine._redis = mock_redis
        unlock_data = {
            "unlock_time": "2026-07-25T12:00:00",
            "unlock_pct_of_supply": 0.005,  # 0.5% < 1% threshold
        }
        mock_redis.get.return_value = json.dumps(unlock_data).encode()
        result = await engine._check_token_unlock("SOL/USDT", "LONG")
        assert result == 0

    @pytest.mark.asyncio
    async def test_unlock_within_window_returns_penalty(self, engine, mock_redis):
        from datetime import datetime, timedelta, timezone
        from app.core.config import get_settings

        # Inject the test's mock_redis into the engine (engine fixture creates its own)
        engine._redis = mock_redis

        # Unlock in 24 hours (within 48h window)
        unlock_time = datetime.now(timezone.utc) + timedelta(hours=24)
        unlock_data = {
            "unlock_time": unlock_time.isoformat(),
            "unlock_pct_of_supply": 0.02,  # 2% > 1% threshold
        }
        mock_redis.get.return_value = json.dumps(unlock_data).encode()

        # Override cached settings attributes directly
        real_settings = get_settings()
        orig_window = real_settings.unlock_window_hours
        orig_threshold = real_settings.unlock_impact_threshold_pct
        orig_penalty = real_settings.unlock_penalty_score
        try:
            real_settings.unlock_window_hours = 48
            real_settings.unlock_impact_threshold_pct = "0.01"
            real_settings.unlock_penalty_score = 30
            result = await engine._check_token_unlock("SOL/USDT", "LONG")
            assert result == 30  # penalty score
        finally:
            real_settings.unlock_window_hours = orig_window
            real_settings.unlock_impact_threshold_pct = orig_threshold
            real_settings.unlock_penalty_score = orig_penalty

    @pytest.mark.asyncio
    async def test_unlock_outside_window_returns_zero(self, engine, mock_redis):
        from datetime import datetime, timedelta, timezone
        from app.core.config import get_settings

        # Inject the test's mock_redis into the engine
        engine._redis = mock_redis

        # Unlock in 72 hours (outside 48h window)
        unlock_time = datetime.now(timezone.utc) + timedelta(hours=72)
        unlock_data = {
            "unlock_time": unlock_time.isoformat(),
            "unlock_pct_of_supply": 0.02,
        }
        mock_redis.get.return_value = json.dumps(unlock_data).encode()

        real_settings = get_settings()
        orig_window = real_settings.unlock_window_hours
        orig_threshold = real_settings.unlock_impact_threshold_pct
        orig_penalty = real_settings.unlock_penalty_score
        try:
            real_settings.unlock_window_hours = 48
            real_settings.unlock_impact_threshold_pct = "0.01"
            real_settings.unlock_penalty_score = 30
            result = await engine._check_token_unlock("SOL/USDT", "LONG")
            assert result == 0
        finally:
            real_settings.unlock_window_hours = orig_window
            real_settings.unlock_impact_threshold_pct = orig_threshold
            real_settings.unlock_penalty_score = orig_penalty


# ─── Half-Kelly with Uncertainty Tests ────────────────────────────────

class TestHalfKellyUncertainty:
    """Tests for Half-Kelly with Uncertainty adjustment."""

    def test_small_sample_reduces_kelly(self):
        sizer = KellySizer()
        base_risk = Decimal("0.015")

        # Only 10 trades (below 30 threshold)
        result = sizer.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=6,
            losses=4,
        )
        # Should reduce risk due to small sample
        assert result < base_risk
        assert result >= MIN_RISK_PCT

    def test_large_sample_no_adjustment(self):
        sizer = KellySizer()
        base_risk = Decimal("0.015")

        # 50 trades (above 30 threshold)
        result = sizer.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=30,
            losses=20,
        )
        # No adjustment for large sample with stable win rate
        assert result == base_risk

    def test_high_variance_reduces_kelly(self):
        sizer = KellySizer()
        base_risk = Decimal("0.015")

        # Create volatile win rate history with variance > 0.15
        # Alternating between 0.2 and 0.8 gives variance ~0.09
        # Need more extreme: 0.1 and 0.9 alternation
        win_rate_history = [0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9,
                           0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9]

        result = sizer.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=30,
            losses=20,
            win_rate_history=win_rate_history,
        )
        # Should reduce risk due to high variance
        assert result < base_risk
        assert result >= MIN_RISK_PCT

    def test_low_variance_no_adjustment(self):
        sizer = KellySizer()
        base_risk = Decimal("0.015")

        # Stable win rate history
        win_rate_history = [0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6,
                           0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6]

        result = sizer.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=30,
            losses=20,
            win_rate_history=win_rate_history,
        )
        # No adjustment for stable win rate
        assert result == base_risk

    def test_result_always_within_bounds(self):
        sizer = KellySizer()
        base_risk = Decimal("0.025")  # Above MAX_RISK_PCT

        result = sizer.apply_uncertainty_adjustment(
            base_risk_pct=base_risk,
            wins=5,
            losses=5,
        )
        assert MIN_RISK_PCT <= result <= MAX_RISK_PCT

    def test_exception_returns_base_risk(self):
        sizer = KellySizer()
        base_risk = Decimal("0.015")

        # Mock get_settings to raise an exception (patch where it's imported locally)
        with patch("app.core.config.get_settings", side_effect=RuntimeError("Config error")):
            result = sizer.apply_uncertainty_adjustment(
                base_risk_pct=base_risk,
                wins=10,
                losses=5,
            )
            # Should return base_risk on exception
            assert result == base_risk
