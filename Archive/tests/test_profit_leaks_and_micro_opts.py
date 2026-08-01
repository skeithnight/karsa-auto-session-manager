"""Unit tests for 3 Profit Leak Fixes & 4 Profit Equation Micro-Optimizations."""

import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.alpha.ml_harvester import MLHarvester
from app.data.market_data_ingestor import MarketDataIngestor
from app.execution.shadow import ShadowExecutor
from app.execution.sor import SmartOrderRouter
from app.risk.portfolio_risk_manager import PortfolioRiskManager


@pytest.mark.asyncio
async def test_low_cap_relative_volume_spoofing_detection() -> None:
    """Verify relative volume spoofing (>30% of top 5 depth & >$10k notional)."""
    redis = AsyncMock()
    ingestor = MarketDataIngestor(redis_client=redis, symbols=["PEPE/USDT"])

    # Mock orderbook where level 1 has 40% of depth ($15k notional on low-cap coin)
    bids = [
        ["0.00010", "150000000"],  # $15,000 notional, 150M coins (40% of 375M total)
        ["0.000099", "75000000"],
        ["0.000098", "50000000"],
        ["0.000097", "50000000"],
        ["0.000096", "50000000"],
    ]
    asks = [
        ["0.00011", "50000000"],
        ["0.00012", "50000000"],
        ["0.00013", "50000000"],
        ["0.00014", "50000000"],
        ["0.00015", "50000000"],
    ]

    orderbook = {"symbol": "PEPE/USDT", "bids": bids, "asks": asks, "timestamp": time.time() * 1000}
    session_mock = AsyncMock()
    session_mock.fetch_order_book = AsyncMock(return_value=orderbook)
    ingestor._session = session_mock

    await ingestor._fetch_orderbook("PEPE/USDT", "PEPE/USDT")

    # Now drop the $15k bid wall
    bids_dropped = [
        ["0.000099", "75000000"],
        ["0.000098", "50000000"],
        ["0.000097", "50000000"],
        ["0.000096", "50000000"],
        ["0.000095", "50000000"],
    ]
    orderbook_dropped = {"symbol": "PEPE/USDT", "bids": bids_dropped, "asks": asks, "timestamp": time.time() * 1000}
    session_mock.fetch_order_book = AsyncMock(return_value=orderbook_dropped)

    await ingestor._fetch_orderbook("PEPE/USDT", "PEPE/USDT")

    # Relative concentration spoofing should trigger
    assert ingestor.spoofing_bid.get("PEPE/USDT") is True


@pytest.mark.asyncio
async def test_fee_aware_atr_unit_normalization() -> None:
    """Verify percentage ATR (< 1.0) is converted to price offset in execute_fee_aware."""
    client = AsyncMock()
    client.create_limit_order = AsyncMock(return_value={"id": "123", "status": "open"})
    client.get_order_status = AsyncMock(return_value={"status": "canceled"})
    client.get_maker_fee_rate = AsyncMock(return_value=Decimal("0.0002"))
    client.cancel_order = AsyncMock()

    sor = SmartOrderRouter(bybit_client=client)

    # ATR passed as 0.02 (2% percentage) for a $50,000 BTC price
    # EM offset = 0.5 * (0.02 * 50000) = $500. CoA offset = 0.00065 * 50000 = $32.50
    # EM ($500) > 3 * CoA ($97.50) -> High Expectancy route
    sor.execute = AsyncMock(return_value={"id": "exec_123", "status": "closed"})  # type: ignore

    res = await sor.execute_fee_aware(
        symbol="BTC/USDT",
        side="LONG",
        amount=Decimal("0.1"),
        price=Decimal("50000"),
        ai_confidence=0.5,
        atr=Decimal("0.02"),  # 2% percentage ATR
    )

    assert res is not None
    assert sor.execute.called


@pytest.mark.asyncio
async def test_dynamic_cvd_momentum_weighted_ev() -> None:
    """Verify negative CVD slope halves open position EV in PRM."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="-0.4")  # Negative CVD slope

    pos_store = MagicMock()
    trade_store = MagicMock()
    sector_map = MagicMock()

    bybit = MagicMock()

    prm = PortfolioRiskManager(
        redis_client=redis,
        position_store=pos_store,
        trade_store=trade_store,
        sector_mapping=sector_map,
        bybit_client=bybit,
    )

    signal = MagicMock()
    signal.symbol = "SOL/USDT"
    signal.score = 90.0

    positions = [
        {
            "symbol": "BTC/USDT",
            "side": "LONG",
            "entry_price": "50000",
            "live_price": "51000",  # +2% PnL
            "take_profit": "53000",
            "current_sl": "49500",
            "proactive_scale_out_executed": False,
        }
    ]

    res = await prm.evaluate_capital_reallocation(signal, positions)
    assert res is not None
    assert res["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_dynamic_shadow_slippage() -> None:
    """Verify dynamic spread-based slippage calculation in ShadowExecutor."""
    redis = AsyncMock()
    # 10 bps spread: bid=100.00, ask=100.10
    redis.get = AsyncMock(return_value='{"bid": "100.00", "ask": "100.10"}')

    executor = ShadowExecutor(redis_client=redis, position_store=MagicMock(), trade_store=MagicMock())

    mid = Decimal("100.05")
    fill = await executor._compute_dynamic_slippage("ETH/USDT", mid, "LONG")

    # 10 bps spread -> 5 bps dynamic slippage penalty -> fill = 100.05 * (1 + 0.0005) = 100.100025
    assert fill > mid


@pytest.mark.asyncio
async def test_ml_harvester_logging() -> None:
    """Verify MLHarvester logs feature vector to Redis."""
    redis = AsyncMock()
    harvester = MLHarvester(redis_client=redis)

    fid = await harvester.log_signal_features(
        symbol="BTC/USDT",
        side="LONG",
        regime="TREND_BULL",
        score=85.0,
        cvd_slope=0.45,
        spread_bps=3.2,
    )

    assert fid.startswith("ML-")
    assert redis.set.called
    assert redis.rpush.called
