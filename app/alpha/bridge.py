"""Alpha Bridge — orchestrates signal generation from market data to TradeSignal.

Bridges MarketConsumer data feeds (candle + market state) to DecisionEngine,
producing actionable TradeSignals. This is the "Key 2: AI Engine" wiring
from ARCHITECTURE.md — no LLM inference, pure deterministic signal pipeline.

Usage:
    bridge = AlphaBridge(decision_engine, emitter)
    signal = await bridge.generate_signal("BTC/USDT", candles, global_state)
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from app.consumer.decision_engine import DecisionEngine, TradeSignal


class AlphaBridge:
    """Orchestrates signal generation from market data to TradeSignal.

    Connects the MarketConsumer's data pipeline to the DecisionEngine,
    adding pre-signal validation and post-signal telemetry.
    """

    _MIN_CANDLES = 50

    def __init__(
        self,
        decision_engine: DecisionEngine,
        emitter: Any = None,
        min_confidence: float = 65.0,
    ) -> None:
        self._engine = decision_engine
        self._emitter = emitter
        self._min_confidence = min_confidence
        self._last_signal: dict[str, TradeSignal] = {}

    async def generate_signal(
        self,
        symbol: str,
        candles: list[list],
        global_state: dict[str, Any] | None = None,
    ) -> TradeSignal | None:
        """Run the full DecisionEngine pipeline and return a validated signal.

        Args:
            symbol: Trading pair (e.g. "BTC/USDT").
            candles: OHLCV array, shape (N,6) with [ts, o, h, l, c, v].
            global_state: Market state from Redis (VWAP, skew, bid/ask). Optional.

        Returns:
            TradeSignal if score meets threshold, else None.
        """
        if len(candles) < self._MIN_CANDLES:
            logger.debug("AlphaBridge: %s insufficient candles (%d/50)", symbol, len(candles))
            return None

        generated_at = time.time()

        try:
            signal = self._engine.evaluate(
                symbol=symbol,
                candles=candles,
                global_prices=self._extract_global_prices(global_state),
                orderbook_delta=self._extract_field(global_state, "orderbook_delta"),
                funding_rate=self._extract_field(global_state, "funding_rate"),
                oi_change=self._extract_field(global_state, "oi_change"),
            )
        except Exception:
            logger.exception("AlphaBridge: evaluate failed for %s", symbol)
            return None

        if signal is None:
            return None

        latency_ms = (time.time() - generated_at) * 1000
        logger.info(
            "AlphaBridge: signal %s %s score=%.1f regime=%s latency=%.0fms",
            symbol, signal.direction, signal.score,
            signal.regime.value, latency_ms,
        )

        if self._emitter is not None:
            try:
                self._emitter.record_signal()
                self._emitter.record_latency(generated_at)
            except Exception:
                pass

        self._last_signal[symbol] = signal
        return signal

    def get_last_signal(self, symbol: str) -> TradeSignal | None:
        """Return the last generated signal for a symbol."""
        return self._last_signal.get(symbol)

    @staticmethod
    def _extract_global_prices(
        global_state: dict[str, Any] | None,
    ) -> dict[str, float] | None:
        """Extract cross-exchange prices for TREND sync scoring."""
        if global_state is None:
            return None
        vwap = global_state.get("global_vwap")
        if vwap is None:
            return None
        return {"vwap": float(vwap)}

    @staticmethod
    def _extract_field(
        global_state: dict[str, Any] | None,
        field: str,
    ) -> float | None:
        """Extract a single field from global state."""
        if global_state is None:
            return None
        val = global_state.get(field)
        return float(val) if val is not None else None
