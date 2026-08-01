"""Unit tests for KASM 2.1 Smart Monolith components (MarketState, MarketAnalyzer, Formatter updates)."""

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # type: ignore[misc]
from datetime import datetime
from decimal import Decimal
try:
    import pytest
except ImportError:
    class DummyMark:
        def asyncio(self, func): return func
    class DummyPytest:
        mark = DummyMark()
    pytest = DummyPytest()  # type: ignore[assignment]

from app.alpha.market_state import MarketState
from app.alpha.market_analyzer import MarketAnalyzer
from app.bot.utils.formatters.live_funnel_formatter import format_live_funnel
from app.bot.utils.formatters.shadow_funnel_formatter import format_shadow_funnel
from app.risk.gates import RiskGate
from app.alpha.strategy_router import StrategyRouter, MarketRegime


def test_market_state_immutability():
    state = MarketState(
        regime="TREND_BULL",
        hmm_prediction="BULL",
        hurst=0.62,
        adx=28.4,
        atr=Decimal("45.20"),
        atr_percentile=75.0,
    )
    assert state.regime == "TREND_BULL"
    assert state.is_degraded is False

    d = state.to_dict()
    assert d["regime"] == "TREND_BULL"
    assert d["hurst"] == 0.62
    assert d["atr"] == "45.20"

    try:
        state.regime = "CHOP"  # type: ignore[misc] # Frozen dataclass guard
        assert False, "Should have raised AttributeError"
    except (AttributeError, TypeError):
        pass


@pytest.mark.asyncio
async def test_market_analyzer_event_driven_update():
    analyzer = MarketAnalyzer()
    candles = [
        [1600000000000 + i * 900000, 100 + i, 105 + i, 95 + i, 102 + i, 1000]
        for i in range(60)
    ]
    state = await analyzer.update_on_candle_close("BTC/USDT", candles)
    assert isinstance(state, MarketState)
    assert analyzer.current_state.timestamp == state.timestamp
    assert analyzer.is_degraded() is False


def test_risk_gate_layers_2_and_7():
    gate = RiskGate()
    # Layer 2: Volatility check
    assert gate.check_volatility_filter(Decimal("100"), Decimal("50"), Decimal("3.0")) is True
    assert gate.check_volatility_filter(Decimal("200"), Decimal("50"), Decimal("3.0")) is False

    # Layer 7: Funding check
    assert gate.check_funding_filter("LONG", Decimal("0.0001")) is True
    assert gate.check_funding_filter("LONG", Decimal("0.001")) is False
    assert gate.check_funding_filter("SHORT", Decimal("-0.001")) is False


def test_alpha_bridge_confluence():
    router = StrategyRouter()
    res = router.check_alpha_confluence(
        hurst=0.60, adx=26.0, volume_surge=True, regime=MarketRegime.TREND_BULL
    )
    assert res is True

    res_chop = router.check_alpha_confluence(
        hurst=0.60, adx=26.0, volume_surge=True, regime=MarketRegime.CHOP
    )
    assert res_chop is False


def test_live_funnel_formatter_kasm21():
    class DummyReport:
        total_trades = 10
        winning_trades = 6
        losing_trades = 4
        win_rate = 60.0
        net_pnl = 150.00
        gross_profit = 200.00
        gross_loss = 50.00
        total_fees = 12.50
        total_slippage = 1.20

    metrics = {
        "regime": "TRENDING_BULL",
        "hmm_prediction": "BULL",
        "hurst": 0.62,
        "adx": 28.4,
        "atr": "45.20",
        "state_freshness_seconds": 12,
        "universe_attempted": 380,
        "alpha_generated": 405,
        "alpha_passed": 94,
        "ai_calls": 45,
        "ai_approvals": 12,
        "risk_passed": 12,
        "trade_orders": 1,
        "trade_exits": 0,
        "event_loop_latency_ms": 4,
        "is_degraded": False,
        "risk_rejections": {"layer_2": 4, "layer_4": 3, "layer_8": 1, "other": 0},
    }

    output = format_live_funnel(metrics, DummyReport())
    assert "🟢 LIVE MODE" in output
    assert "🟢 NORMAL" in output
    assert "MARKET STATE" in output
    assert "Hurst: 0.62" in output
    assert "Alpha Bridge (MTF)" in output
    assert "9-LAYER RISK REJECTIONS" in output


def test_shadow_funnel_formatter_kasm21():
    class DummyReport:
        total_trades = 5
        winning_trades = 2
        losing_trades = 3
        win_rate = 40.0
        net_pnl = -10.00
        gross_profit = 20.00
        gross_loss = 30.00
        total_fees = 0.0
        total_slippage = 0.0

    metrics = {
        "regime": "CHOPPY",
        "hmm_prediction": "NEUTRAL",
        "hurst": 0.48,
        "adx": 18.2,
        "atr": "32.10",
        "state_freshness_seconds": 45,
        "universe_attempted": 390,
        "alpha_generated": 415,
        "alpha_passed": 98,
        "ai_calls": 59,
        "ai_approvals": 15,
        "risk_passed": 15,
        "trade_orders": 4,
        "trade_exits": 0,
        "event_loop_latency_ms": 3,
        "is_degraded": False,
        "risk_rejections": {"layer_3": 6, "layer_4": 3, "layer_6": 2, "other": 0},
    }

    output = format_shadow_funnel(metrics, DummyReport())
    assert "👥 SHADOW MODE" in output
    assert "🟢 NORMAL" in output
    assert "MARKET STATE" in output
    assert "Layer 3 (MTF Alignment):6 rejections" in output


def test_hmm_pretrained_files_exist():
    import os
    for sym in ["BTC_USDT", "ETH_USDT", "SOL_USDT", "DEFAULT"]:
        path = os.path.join("models", f"hmm_{sym}.pkl")
        assert os.path.exists(path) is True


def test_telemetry_emitter_record_risk_rejection():
    from app.core.telemetry import TelemetryEmitter
    emitter = TelemetryEmitter(redis_client=None, service_name="test-service")
    emitter.record_risk_rejection(layer=4)
    emitter.record_risk_rejection(layer="2")
    # Verified metric recording without raising exception


def test_shadow_mode_toggle_setting():
    from app.core.config import get_settings
    settings = get_settings()
    assert hasattr(settings, "shadow_mode_enabled")


if __name__ == "__main__":
    import asyncio
    print("Running KASM 2.1 Smart Monolith Validation Suite...")
    test_market_state_immutability()
    print("  ✅ MarketState immutability test passed")
    asyncio.run(test_market_analyzer_event_driven_update())
    print("  ✅ MarketAnalyzer event-driven update test passed")
    test_risk_gate_layers_2_and_7()
    print("  ✅ RiskGate Layers 2 & 7 test passed")
    test_alpha_bridge_confluence()
    print("  ✅ Alpha Bridge Confluence Voting test passed")
    test_live_funnel_formatter_kasm21()
    print("  ✅ Live Funnel Formatter test passed")
    test_shadow_funnel_formatter_kasm21()
    print("  ✅ Shadow Funnel Formatter test passed")
    test_hmm_pretrained_files_exist()
    print("  ✅ HMM Pre-trained files existence test passed")
    test_telemetry_emitter_record_risk_rejection()
    print("  ✅ TelemetryEmitter risk rejection recording test passed")
    test_shadow_mode_toggle_setting()
    print("  ✅ Shadow Mode toggle setting test passed")
    print("\n🎉 ALL KASM 2.1 SMART MONOLITH TESTS PASSED SUCCESSFULLY!")

