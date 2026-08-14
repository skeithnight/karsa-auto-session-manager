"""AlphaBridge — wraps and orchestrates DecisionEngine signal evaluation."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.core import metrics


class AlphaBridge:
    """Orchestrates signal generation from DecisionEngine."""

    MIN_CANDLES = 50

    def __init__(self, decision_engine: Any, emitter: Any | None = None) -> None:
        self._engine = decision_engine
        self._emitter = emitter
        self._last_signals: dict[str, Any] = {}

    def get_last_signal(self, symbol: str) -> Any | None:
        """Get the most recent signal evaluated for a symbol."""
        return self._last_signals.get(symbol)

    @staticmethod
    def _extract_global_prices(global_state: dict[str, Any] | None) -> dict[str, float] | None:
        """Extract vwap price dict from global state."""
        if not global_state or "global_vwap" not in global_state:
            return None
        try:
            return {"vwap": float(global_state["global_vwap"])}
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_field(global_state: dict[str, Any] | None, field_name: str) -> float | None:
        """Extract a float field from global state."""
        if not global_state or field_name not in global_state:
            return None
        try:
            val = global_state[field_name]
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    async def generate_signal(
        self,
        symbol: str,
        candles: list[Any],
        global_state: dict[str, Any] | None = None,
    ) -> Any | None:
        """Evaluate candles through the decision engine."""
        if len(candles) < self.MIN_CANDLES:
            logger.debug(f"AlphaBridge: insufficient candles for {symbol} ({len(candles)} < {self.MIN_CANDLES})")
            return None

        global_prices = self._extract_global_prices(global_state)
        orderbook_delta = self._extract_field(global_state, "orderbook_delta")
        funding_rate = self._extract_field(global_state, "funding_rate")
        oi_change = self._extract_field(global_state, "oi_change")

        try:
            signal = self._engine.evaluate(
                symbol=symbol,
                candles=candles,
                global_prices=global_prices,
                orderbook_delta=orderbook_delta,
                funding_rate=funding_rate,
                oi_change=oi_change,
            )
            # Support both async and sync evaluate methods
            if hasattr(signal, "__await__"):
                signal = await signal

            if signal is not None:
                self._last_signals[symbol] = signal
                if self._emitter and hasattr(self._emitter, "emit"):
                    self._emitter.emit("signal_generated", {"symbol": symbol, "signal": signal})

            return signal
        except Exception as e:
            logger.warning(f"AlphaBridge evaluate exception for {symbol}: {e}")
            return None
