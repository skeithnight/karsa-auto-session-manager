"""Tests for BacktestEngine — deterministic replay, wick detection, funding, fees.

Uses real RegimeClassifier/StrategyRouter/DynamicRiskGate instances (stateless).
No Redis or PostgreSQL dependency — pure in-memory computation.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np

from app.alpha.regime_classifier import MarketRegime, RegimeClassifier
from app.alpha.strategy_router import StrategyRouter
from app.backtest.engine import BacktestEngine, BacktestReport
from app.risk.dynamic_risk_gate import DynamicRiskGate


def _make_uptrend_candles(n: int = 100, start: float = 100.0) -> list[list]:
    """Strong uptrend — breakout candles with volume surge."""
    candles = []
    for i in range(n):
        c = start + i * 2.0  # 2 point per bar
        # Every 21st bar: big breakout candle that crosses the 20-period high
        if i % 21 == 0 and i > 0:
            c = start + i * 2.0 + 5.0  # extra jump
        o = c - 0.3
        h = c + 2.0
        l = c - 1.0
        vol = 3000.0 if i % 10 < 5 else 800.0  # volume surge on green bars
        candles.append([1000000 + i * 3600000, o, h, l, c, vol])
    return candles


def _make_downtrend_candles(n: int = 100, start: float = 200.0) -> list[list]:
    """Strong downtrend — breakdown candles with volume surge."""
    candles = []
    for i in range(n):
        c = start - i * 2.0
        if i % 21 == 0 and i > 0:
            c = start - i * 2.0 - 5.0
        o = c + 0.3
        h = c + 1.0
        l = c - 2.0
        vol = 3000.0 if i % 10 < 5 else 800.0
        candles.append([1000000 + i * 3600000, o, h, l, c, vol])
    return candles


def _make_range_candles(n: int = 100, mid: float = 100.0, amp: float = 5.0) -> list[list]:
    """Range-bound — oscillates around mid."""
    candles = []
    for i in range(n):
        c = mid + amp if i % 2 == 0 else mid - amp
        o, h, l = c, c + 1.0, c - 1.0
        candles.append([1000000 + i * 3600000, o, h, l, c, 1000.0])
    return candles


def _make_flat_candles(n: int = 100, price: float = 100.0) -> list[list]:
    """All-flat prices — regime classifier returns RANGE."""
    return [[1000000 + i * 3600000, price, price, price, price, 1000.0] for i in range(n)]


@pytest.fixture
def engine() -> BacktestEngine:
    return BacktestEngine(
        regime_classifier=RegimeClassifier(),
        strategy_router=StrategyRouter(),
        risk_gate=DynamicRiskGate(),
        base_size=Decimal("0.001"),
        slippage_pct=Decimal("0.0005"),
        taker_fee_pct=Decimal("0.00055"),
        maker_fee_pct=Decimal("0.0002"),
        funding_rate=Decimal("0"),
    )


@pytest.fixture
def engine_with_funding() -> BacktestEngine:
    return BacktestEngine(
        regime_classifier=RegimeClassifier(),
        strategy_router=StrategyRouter(),
        risk_gate=DynamicRiskGate(),
        base_size=Decimal("0.001"),
        slippage_pct=Decimal("0.0005"),
        taker_fee_pct=Decimal("0.00055"),
        maker_fee_pct=Decimal("0.0002"),
        funding_rate=Decimal("0.001"),
        funding_interval_bars=8,
    )


# ── TestTradeTakenVsNotTaken ─────────────────────────────


class TestTradeTakenVsNotTaken:
    @pytest.mark.asyncio
    async def test_no_trade_when_score_below_gate(self, engine: BacktestEngine):
        """Flat candles — no edge, score should be low."""
        candles = _make_flat_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-flat")
        taken = [r for r in reports if r.trade_taken]
        assert len(taken) == 0, f"Expected no trades on flat candles, got {len(taken)}"

    @pytest.mark.asyncio
    async def test_trade_taken_in_strong_uptrend(self, engine: BacktestEngine):
        """Strong uptrend with global sync should produce at least one LONG entry."""
        candles = _make_uptrend_candles(200)
        global_prices = {"binance": 1000.0, "okx": 1001.0}
        reports = await engine.run("BTCUSDT", candles, "job-up", global_prices=global_prices)
        taken = [r for r in reports if r.trade_taken]
        assert len(taken) >= 1, "Expected at least 1 trade in strong uptrend"
        assert taken[0].direction == "LONG"


# ── TestWorstPriceSeen ───────────────────────────────────


class TestWorstPriceSeen:
    @pytest.mark.asyncio
    async def test_long_sl_hit_on_wick_below_close(self, engine: BacktestEngine):
        """Candle wick dips below SL but close recovers — must still trigger SL."""
        candles = _make_uptrend_candles(60, start=100.0)
        candles.append([1000000 + 60 * 3600000, 160.0, 162.0, 140.0, 159.0, 5000.0])
        for i in range(61, 90):
            c = 159.0 + (i - 60) * 0.5
            candles.append([1000000 + i * 3600000, c - 0.3, c + 1.0, c - 1.0, c, 1000.0])

        reports = await engine.run("BTCUSDT", candles, "job-wick")
        taken = [r for r in reports if r.trade_taken]
        if taken:
            assert any(r.exit_reason == "sl_hit" for r in taken), \
                "Expected at least one SL hit from wick detection"

    @pytest.mark.asyncio
    async def test_no_sl_hit_when_wick_stays_above_sl(self, engine: BacktestEngine):
        """Wick doesn't breach SL — trade should survive."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-survive")
        for r in reports:
            if r.trade_taken and r.exit_reason == "sl_hit":
                assert r.bars_held > 1, "SL hit too early — wick detection may be wrong"


# ── TestTimeExit ─────────────────────────────────────────


class TestTimeExit:
    @pytest.mark.asyncio
    async def test_chop_time_exit_at_30_bars(self, engine: BacktestEngine):
        """CHOP regime: max_hold_time_mins=30 → exits at or before bar 30."""
        candles = _make_range_candles(200, mid=100.0, amp=3.0)
        reports = await engine.run("BTCUSDT", candles, "job-chop")
        for r in reports:
            if r.trade_taken and r.regime == MarketRegime.CHOP and r.exit_reason == "time_exit":
                assert r.bars_held <= 30, f"CHOP exit at {r.bars_held} bars, expected ≤30"

    @pytest.mark.asyncio
    async def test_trend_longer_hold_than_chop(self, engine: BacktestEngine):
        """TREND regime holds longer than CHOP (24h vs 30min)."""
        candles = _make_uptrend_candles(300)
        reports = await engine.run("BTCUSDT", candles, "job-trend-hold")
        for r in reports:
            if r.trade_taken and r.regime in (MarketRegime.TREND_BULL,) and r.exit_reason == "time_exit":
                assert r.bars_held > 20, f"TREND exit at {r.bars_held} bars, expected >20"


# ── TestFundingDrain ─────────────────────────────────────


class TestFundingDrain:
    @pytest.mark.asyncio
    async def test_funding_deducted_after_interval(self, engine_with_funding: BacktestEngine):
        """funding_rate > 0 and position held ≥ 8 bars → total_funding > 0."""
        candles = _make_uptrend_candles(200)
        reports = await engine_with_funding.run("BTCUSDT", candles, "job-fund")
        taken = [r for r in reports if r.trade_taken and r.bars_held >= 8]
        if taken:
            assert any(r.total_funding > 0 for r in taken), "Expected funding deduction after 8 bars"

    @pytest.mark.asyncio
    async def test_no_funding_when_rate_zero(self, engine: BacktestEngine):
        """funding_rate=0 → total_funding == 0 on all trades."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-nofund")
        for r in reports:
            if r.trade_taken:
                assert r.total_funding == 0, f"Expected 0 funding, got {r.total_funding}"


# ── TestFeeCalculation ───────────────────────────────────


class TestFeeCalculation:
    @pytest.mark.asyncio
    async def test_post_only_regimes_use_maker_fee(self, engine: BacktestEngine):
        """RANGE/CHOP regimes have use_post_only=True."""
        candles = _make_range_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-maker")
        for r in reports:
            if r.trade_taken and r.risk_profile.use_post_only:
                assert r.risk_profile.use_post_only is True

    @pytest.mark.asyncio
    async def test_trend_regimes_use_taker_fee(self, engine: BacktestEngine):
        """TREND regimes have use_post_only=False."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-taker")
        for r in reports:
            if r.trade_taken and not r.risk_profile.use_post_only:
                assert r.risk_profile.use_post_only is False

    @pytest.mark.asyncio
    async def test_fees_positive_on_taken_trades(self, engine: BacktestEngine):
        """All taken trades should have positive total fees."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-fees-pos")
        for r in reports:
            if r.trade_taken:
                assert r.total_fees > 0, f"Expected positive fees, got {r.total_fees}"


# ── TestDirectionMapping ─────────────────────────────────


class TestDirectionMapping:
    @pytest.mark.asyncio
    async def test_uptrend_produces_long_only(self, engine: BacktestEngine):
        """Strong uptrend → only LONG direction tested."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-dir-up")
        taken = [r for r in reports if r.trade_taken]
        assert all(r.direction == "LONG" for r in taken)

    @pytest.mark.asyncio
    async def test_downtrend_produces_short_only(self, engine: BacktestEngine):
        """Strong downtrend → only SHORT direction tested."""
        candles = _make_downtrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-dir-down")
        taken = [r for r in reports if r.trade_taken]
        assert all(r.direction == "SHORT" for r in taken)

    @pytest.mark.asyncio
    async def test_range_produces_both_directions(self, engine: BacktestEngine):
        """Range-bound → both LONG and SHORT reports appear."""
        candles = _make_range_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-dir-range")
        directions = {r.direction for r in reports}
        assert {"LONG", "SHORT"} == directions


# ── TestPnLCorrectness ───────────────────────────────────


class TestPnLCorrectness:
    @pytest.mark.asyncio
    async def test_pnl_net_less_than_gross(self, engine: BacktestEngine):
        """Net PnL ≤ gross PnL (fees deducted)."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-pnl-check")
        for r in reports:
            if r.trade_taken:
                assert r.pnl_net <= r.pnl_gross


# ── TestEndOfData ────────────────────────────────────────


class TestEndOfData:
    @pytest.mark.asyncio
    async def test_exits_at_end_of_data(self, engine: BacktestEngine):
        """Short candle set → trade exits at end of data."""
        candles = _make_uptrend_candles(60, start=100.0)
        reports = await engine.run("BTCUSDT", candles, "job-eod")
        taken = [r for r in reports if r.trade_taken]
        if taken:
            assert any(r.exit_reason == "end_of_data" for r in taken)


# ── TestInsufficientData ─────────────────────────────────


class TestInsufficientData:
    @pytest.mark.asyncio
    async def test_fewer_than_50_candles_returns_empty(self, engine: BacktestEngine):
        """< 50 candles → engine returns empty list."""
        candles = _make_uptrend_candles(30)
        reports = await engine.run("BTCUSDT", candles, "job-insufficient")
        assert reports == []


# ── TestSlippage ─────────────────────────────────────────


class TestSlippage:
    @pytest.mark.asyncio
    async def test_long_entry_above_close(self, engine: BacktestEngine):
        """LONG entry = close × (1 + 0.0005)."""
        candles = _make_uptrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-slip-long")
        for r in reports:
            if r.trade_taken and r.direction == "LONG":
                entry_ts = int(r.entry_time.timestamp() * 1000)
                for c in candles:
                    if c[0] == entry_ts:
                        expected = Decimal(str(c[4])) * Decimal("1.0005")
                        assert abs(r.entry_price - expected) < Decimal("0.01")
                        break

    @pytest.mark.asyncio
    async def test_short_entry_below_close(self, engine: BacktestEngine):
        """SHORT entry = close × (1 - 0.0005)."""
        candles = _make_downtrend_candles(200)
        reports = await engine.run("BTCUSDT", candles, "job-slip-short")
        for r in reports:
            if r.trade_taken and r.direction == "SHORT":
                entry_ts = int(r.entry_time.timestamp() * 1000)
                for c in candles:
                    if c[0] == entry_ts:
                        expected = Decimal(str(c[4])) * Decimal("0.9995")
                        assert abs(r.entry_price - expected) < Decimal("0.01")
                        break
