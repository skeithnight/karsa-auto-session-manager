"""Unit tests for AI Analyst flow context & short squeeze boost."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.alpha.analyst import CryptoAnalyst, AnalystResult


@pytest.mark.asyncio
async def test_analyst_short_squeeze_confluence_boost():
    """Verify +20% boost when LONG signal coincides with USDT inflow >= 10M & liq volume >= 20M."""
    ai_client = AsyncMock()
    ai_client.complete = AsyncMock(
        return_value='{"confidence_score": 70, "decision_recommendation": "STRONG_BUY", "primary_edge": "Breakout", "critical_risk_flag": "None"}'
    )
    ai_client.model = "test-model"

    fetcher = AsyncMock()
    # 60 candles: [time, open, high, low, close, volume]
    fetcher.fetch = AsyncMock(
        return_value=[[i, 100, 105, 95, 100, 1000] for i in range(60)]
    )

    analyst = CryptoAnalyst(ai_client=ai_client, ohlcv_fetcher=fetcher)

    flow_data = {
        "usdt_inflow_m": 15.0,
        "btc_outflow_count": 500.0,
        "liq_volume_m": 45.0,
        "liq_dominant_side": "Longs",
    }

    result = await analyst.analyze(
        symbol="BTC/USDT",
        direction="LONG",
        confidence=0.7,
        regime="TREND_BULL",
        spread_pct=0.0005,
        funding_rate=0.0001,
        oi_change=0.05,
        price=Decimal("60000.0"),
        flow_data=flow_data,
    )

    assert result is not None
    # Original 70 + 20 boost = 90
    assert result.ai_confidence == 90
    assert "Boost: +20% short squeeze setup" in result.reasoning
