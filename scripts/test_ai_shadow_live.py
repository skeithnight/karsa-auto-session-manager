"""Test Live AI Integration in Shadow Mode via 9router Proxy."""

import asyncio
import json
import numpy as np
from decimal import Decimal
from loguru import logger

from app.core.config import get_settings
from app.core.ai_client import AIClient
from app.alpha.analyst import CryptoAnalyst
from app.data.ohlcv_fetcher import OHLCVFetcher


class MockExchange:
    async def fetch_ohlcv(self, symbol, timeframe="1h", limit=60):
        prices = np.linspace(0.040, 0.045, limit)
        candles = []
        for i in range(limit):
            candles.append([i * 3600, float(prices[i] - 0.001), float(prices[i] + 0.002), float(prices[i] - 0.002), float(prices[i]), 1000.0])
        return candles


async def test_live_ai():
    logger.info("=== VALIDATING LIVE AI LAYER IN SHADOW MODE ===")
    settings = get_settings()
    logger.info(f"9Router Base URL: {settings.nine_router_base_url}")
    logger.info(f"9Router Model: {settings.nine_router_model}")
    logger.info(f"9Router Auth Token Set: {bool(settings.nine_router_auth_token)}")

    ai_client = AIClient(
        router_url=settings.nine_router_base_url,
        auth_token=settings.nine_router_auth_token,
        model=settings.nine_router_model,
        timeout_seconds=30.0,
    )

    ccxt = MockExchange()
    fetcher = OHLCVFetcher(exchange=ccxt)
    analyst = CryptoAnalyst(ai_client=ai_client, ohlcv_fetcher=fetcher, is_shadow=True)

    logger.info("\n1. Testing AIClient.complete() directly to 9Router container...")
    raw_reply = await ai_client.complete(
        prompt="Analyze 1000RATS/USDT LONG entry signal. Regime: MOMENTUM_SQUEEZE. Price: 0.045",
        system_prompt="You are a crypto analyst. Respond with JSON containing confidence_score and decision_recommendation."
    )
    logger.info(f"Raw 9router Response:\n{raw_reply}")
    assert raw_reply is not None, "AIClient failed to receive response from 9router!"

    logger.info("\n2. Testing Pre-entry CryptoAnalyst via 9router...")
    analyst_result = await analyst.analyze(
        symbol="1000RATS/USDT",
        direction="LONG",
        confidence=0.80,
        regime="MOMENTUM_SQUEEZE",
        spread_pct=0.001,
        funding_rate=-0.0002,
        oi_change=0.05,
        price=Decimal("0.045"),
    )

    if analyst_result:
        logger.info("✅ Pre-entry CryptoAnalyst Verdict SUCCESS:")
        logger.info(f"   AI Confidence: {analyst_result.ai_confidence}%")
        logger.info(f"   Recommendation: {analyst_result.decision_recommendation}")
        logger.info(f"   Reasoning: {analyst_result.reasoning}")
        logger.info(f"   Model Used: {analyst_result.model_used}")
    else:
        logger.error("❌ CryptoAnalyst returned None")
        return False

    # Blended confidence math
    ai_conf = analyst_result.ai_confidence / 100.0
    quant_conf = 0.80
    blended = quant_conf * 0.5 + ai_conf * 0.5
    assert analyst_result is not None, "Analyst result should not be None"
    logger.info("✅ Pre-entry AI Gate Evaluation Completed Successfully!")

    logger.info("\n=== LIVE AI LAYER VALIDATION 100% SUCCESSFUL ===")
    return True


if __name__ == "__main__":
    asyncio.run(test_live_ai())
