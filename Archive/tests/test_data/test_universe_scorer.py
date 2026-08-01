"""Tests for UniverseScorer."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.data.universe_scorer import UniverseScorer


def _make_candles(n: int, start_price: float = 100.0, step: float = 1.0):
    """Build n OHLCV candles with ascending close prices. Each candle is
    [timestamp_ms, open, high, low, close, volume]."""
    candles = []
    for i in range(n):
        close = start_price + step * i
        candles.append(
            [1_700_000_000_000 + i * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
        )
    return candles


def _make_state(total_volume: float = 500_000_000.0):
    """Build a minimal global state dict returned by redis.get_global_state."""
    return {"total_volume": total_volume}


def _mock_scorer(symbols=None, top_n=15, min_score=Decimal("55"), max_per_sector=2):
    """Create a UniverseScorer with mock redis and fetcher.

    Constructor stores: self.redis = redis_client, self.fetcher = ohlcv_fetcher.
    """
    redis_mock = SimpleNamespace(get_global_state=AsyncMock())
    fetcher_mock = SimpleNamespace(fetch=AsyncMock(), fetch_funding_rate=AsyncMock(return_value=Decimal("0.0001")))
    if symbols is None:
        symbols = ["BTC/USDT"]
    scorer = UniverseScorer(
        redis_client=redis_mock,
        ohlcv_fetcher=fetcher_mock,
        symbols=symbols,
        top_n=top_n,
        min_score=min_score,
        max_per_sector=max_per_sector,
    )
    return scorer


class TestScoreSymbol:
    def setup_method(self):
        self.scorer = _mock_scorer(symbols=["BTC/USDT"])
        self.scorer.redis.get_global_state.return_value = _make_state()
        self.scorer.fetcher.fetch.return_value = _make_candles(168)

    @pytest.mark.asyncio
    async def test_score_symbol_basic(self):
        """Score dict must contain all required keys with sensible values."""
        result = await self.scorer.score_symbol("BTC/USDT")

        assert result is not None
        for key in ("symbol", "volume_score", "momentum_score", "squeeze_score",
                     "overextension_penalty", "total_score", "sector"):
            assert key in result, f"missing key: {key}"
        assert result["symbol"] == "BTC/USDT"
        assert result["sector"] == "MAJORS"

    @pytest.mark.asyncio
    async def test_score_symbol_no_state(self):
        """When redis returns None the scorer must return None."""
        self.scorer.redis.get_global_state.return_value = None
        result = await self.scorer.score_symbol("BTC/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_score_symbol_zero_volume(self):
        """Zero global volume should yield None (division guard)."""
        self.scorer.redis.get_global_state.return_value = _make_state(total_volume=0)
        result = await self.scorer.score_symbol("BTC/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_score_symbol_insufficient_candles(self):
        """Fewer than 21 candles should return None (need 21 for BB + momentum)."""
        self.scorer.fetcher.fetch.return_value = _make_candles(10)
        result = await self.scorer.score_symbol("BTC/USDT")
        assert result is None


class TestOverextension:
    @pytest.mark.asyncio
    async def test_overextension_penalty(self):
        """Last close >30% above candle[-21] must produce a negative penalty."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # 168 candles with V-shape to avoid triggering strong_performer:
        # - Candles 1-120: flat at 100
        # - Candles 121-147: drop to 70 (candle[-21] = 70)
        # - Candles 148-168: recover to 100 (candle[-1] = 100)
        # This way:
        # - overextension: |100 - 70| / 70 = 42.9% > 30% ✓
        # - strong_performer: (100 - 100) / 100 = 0% < 15% ✓ (candle[-48] ≈ 100)
        candles = _make_candles(120, start_price=100.0, step=0.1)
        # Drop from 100 to 70 over 27 candles
        for i in range(27):
            close = 100.0 - (i + 1) * (30.0 / 27)  # 98.89, 97.78, ..., 70.0
            candles.append(
                [1_700_000_000_000 + (120 + i) * 900_000,
                 close - 0.5, close + 1.0, close - 1.0, close, 2_000_000.0]
            )
        # Recover from 70 to 100 over 21 candles
        for i in range(21):
            close = 70.0 + (i + 1) * (30.0 / 21)  # 71.43, 72.86, ..., 100.0
            candles.append(
                [1_700_000_000_000 + (147 + i) * 900_000,
                 close - 0.5, close + 1.0, close - 1.0, close, 2_000_000.0]
            )
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        assert result["overextension_penalty"] < 0


class TestSelectSectorCap:
    @pytest.mark.asyncio
    async def test_select_sector_cap(self):
        """With max_per_sector=2, at most 2 MAJORS symbols should be selected."""
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]  # MAJORS, MAJORS, L1
        scorer = _mock_scorer(symbols=symbols, top_n=15, max_per_sector=2)

        async def fake_score(sym):
            sector = {"BTC/USDT": "MAJORS", "ETH/USDT": "MAJORS", "SOL/USDT": "L1"}[sym]
            return {
                "symbol": sym, "volume_score": Decimal("30"),
                "momentum_score": Decimal("40"), "squeeze_score": Decimal("30"),
                "overextension_penalty": Decimal("0"), "total_score": Decimal("100"),
                "sector": sector,
            }

        with patch.object(scorer, "score_symbol", side_effect=fake_score):
            selected = await scorer.select()

        majors_count = sum(1 for s in selected if s["sector"] == "MAJORS")
        assert majors_count <= 2


class TestSelectTopN:
    @pytest.mark.asyncio
    async def test_select_top_n(self):
        """With top_n=5, only 5 symbols should be returned."""
        symbols = [f"SYM{i}/USDT" for i in range(20)]
        scorer = _mock_scorer(symbols=symbols, top_n=5, min_score=Decimal("0"), max_per_sector=10)

        async def fake_score(sym):
            idx = int(sym.replace("SYM", "").replace("/USDT", ""))
            return {
                "symbol": sym, "volume_score": Decimal("30"),
                "momentum_score": Decimal("40"), "squeeze_score": Decimal("30"),
                "overextension_penalty": Decimal("0"), "total_score": Decimal(str(100 - idx)),
                "sector": "L1",
            }

        with patch.object(scorer, "score_symbol", side_effect=fake_score):
            selected = await scorer.select()

        assert len(selected) == 5


class TestSelectMinScore:
    @pytest.mark.asyncio
    async def test_select_min_score(self):
        """Symbols below min_score must be excluded."""
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        scorer = _mock_scorer(symbols=symbols, top_n=15, min_score=Decimal("60"), max_per_sector=10)

        scores = {
            "BTC/USDT": Decimal("80"),
            "ETH/USDT": Decimal("50"),  # below threshold
            "SOL/USDT": Decimal("70"),
        }

        async def fake_score(sym):
            sector = {"BTC/USDT": "MAJORS", "ETH/USDT": "MAJORS", "SOL/USDT": "L1"}[sym]
            return {
                "symbol": sym, "volume_score": Decimal("0"),
                "momentum_score": Decimal("0"), "squeeze_score": Decimal("0"),
                "overextension_penalty": Decimal("0"), "total_score": scores[sym],
                "sector": sector,
            }

        with patch.object(scorer, "score_symbol", side_effect=fake_score):
            selected = await scorer.select()

        selected_syms = [s["symbol"] for s in selected]
        assert "ETH/USDT" not in selected_syms
        assert "BTC/USDT" in selected_syms
        assert "SOL/USDT" in selected_syms


class TestRefreshFallback:
    @pytest.mark.asyncio
    async def test_refresh_fallback(self):
        """When select returns empty, refresh must fall back to config_symbols."""
        scorer = _mock_scorer(symbols=["BTC/USDT"], top_n=5, min_score=Decimal("100"))
        scorer.redis.get_global_state.return_value = _make_state()
        # refresh writes to redis.redis.set(...)
        scorer.redis.set = AsyncMock()

        # select will return [] because score_symbol returns None (insufficient candles)
        scorer.fetcher.fetch.return_value = _make_candles(5)

        config_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT", "XRP/USDT"]
        result = await scorer.refresh(config_symbols)

        assert result == config_symbols[:5]
        assert len(result) == 5
        scorer.redis.set.assert_called_once()


class TestVolumeAnomaly:
    """Test volume anomaly detection (Phase 2)."""

    @pytest.mark.asyncio
    async def test_volume_anomaly_detected(self):
        """3x volume + stable price should trigger anomaly bonus."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # Build 168 candles (7 days) with:
        # - First 164 candles: normal volume (1M)
        # - Candles 165-167: low volume (0.5M)
        # - Last candle (168): spike to 4.5M (ratio > 3x vs avg of last 4)
        candles = []
        for i in range(164):
            close = 100.0 + (i % 5) * 0.1  # Oscillate around 100
            candles.append(
                [1_700_000_000_000 + i * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
            )
        # Candles 165-167: low volume (0.5M), price stable
        for i in range(3):
            close = 100.0 + i * 0.01
            candles.append(
                [1_700_000_000_000 + (164 + i) * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 500_000.0]
            )
        # Last candle (168): 5M volume, price stable
        # avg of last 4 = (0.5M + 0.5M + 0.5M + 5M) / 4 = 1.625M, ratio = 5M / 1.625M = 3.08
        close = 100.03
        candles.append(
            [1_700_000_000_000 + 167 * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 5_000_000.0]
        )
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        assert result["volume_anomaly_score"] > 0

    @pytest.mark.asyncio
    async def test_no_volume_anomaly_normal(self):
        """Normal volume should not trigger anomaly."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # All candles with same volume
        candles = _make_candles(168, start_price=100.0, step=0.1)
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        assert result["volume_anomaly_score"] == 0

    @pytest.mark.asyncio
    async def test_volume_anomaly_price_moved(self):
        """3x volume + price moved >5% should NOT trigger anomaly (not accumulation)."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # Build 168 candles with price ramping up significantly in last 4
        candles = _make_candles(164, start_price=100.0, step=0.1)
        # Last 4 candles: 3x volume, price jumping 6%
        for i in range(4):
            close = 106.0 + i * 0.5  # Big price move
            candles.append(
                [1_700_000_000_000 + (164 + i) * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 3_000_000.0]
            )
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        # Price moved >5%, so no anomaly bonus
        assert result["volume_anomaly_score"] == 0


class TestStrongPerformer:
    """Test strong performer flag (Phase 2, prep for Phase 3 Dip Buyer)."""

    @pytest.mark.asyncio
    async def test_strong_performer_flag(self):
        """+15% in 48h should set strong_performer=True and disable overextension penalty."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # Build 168 candles: first 120 flat, last 48 ramping up 15%+
        candles = []
        for i in range(120):
            close = 100.0
            candles.append(
                [1_700_000_000_000 + i * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
            )
        for i in range(48):
            close = 100.0 + (i + 1) * 0.35  # Ramp from 100.35 to 117.15 (~17%)
            candles.append(
                [1_700_000_000_000 + (120 + i) * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
            )
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        assert result["strong_performer"] is True
        # Overextension penalty should be disabled for strong performers
        assert result["overextension_penalty"] == 0

    @pytest.mark.asyncio
    async def test_not_strong_performer(self):
        """+10% in 48h should NOT set strong_performer (below 15% threshold)."""
        scorer = _mock_scorer(symbols=["BTC/USDT"])
        scorer.redis.get_global_state.return_value = _make_state()

        # Build 168 candles: first 120 flat, last 48 ramping up only 10%
        candles = []
        for i in range(120):
            close = 100.0
            candles.append(
                [1_700_000_000_000 + i * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
            )
        for i in range(48):
            close = 100.0 + (i + 1) * 0.21  # Ramp from 100.21 to 110.29 (~10%)
            candles.append(
                [1_700_000_000_000 + (120 + i) * 900_000, close - 0.5, close + 1.0, close - 1.0, close, 1_000_000.0]
            )
        scorer.fetcher.fetch.return_value = candles

        result = await scorer.score_symbol("BTC/USDT")
        assert result is not None
        assert result["strong_performer"] is False
