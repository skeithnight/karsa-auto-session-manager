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
        self.scorer.fetcher.fetch.return_value = _make_candles(25)

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

        # 25 candles: first 21 at ~100, last 4 ramp to 140 (>30% above 100)
        candles = _make_candles(21, start_price=100.0, step=0.5)
        for i in range(4):
            close = 131.0 + i * 3  # 131, 134, 137, 140
            candles.append(
                [1_700_000_000_000 + (21 + i) * 900_000,
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
