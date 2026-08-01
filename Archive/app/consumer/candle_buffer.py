"""Rolling candle buffer per symbol for the consumer pipeline.

Maintains a sliding window of the most recent N candles per symbol.
Provides numpy array views for RegimeClassifier and StrategyRouter.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_BUFFER_SIZE = 200


class CandleBuffer:
    """Per-symbol rolling window of OHLCV candles.

    Thread-safe under asyncio (single event loop). Each symbol maintains
    its own deque, capped at `max_size` entries.

    Attributes:
        max_size: Maximum candles retained per symbol.
    """

    def __init__(self, max_size: int = _DEFAULT_BUFFER_SIZE) -> None:
        self.max_size = max_size
        self._buffers: dict[str, deque[list]] = defaultdict(
            lambda: deque(maxlen=max_size)
        )

    def append(self, symbol: str, candle: list) -> None:
        """Append a single OHLCV candle for a symbol.

        Candle format: [timestamp_ms, open, high, low, close, volume].
        Deduplicates by timestamp — same ts replaces previous candle.

        Args:
            symbol: Unified symbol (e.g. BTC/USDT).
            candle: Raw ccxt OHLCV list.
        """
        buf = self._buffers[symbol]
        ts = int(candle[0])

        # Dedup: if last candle has same ts, replace it
        if buf and int(buf[-1][0]) == ts:
            buf[-1] = candle
            return

        buf.append(candle)

    def as_numpy(self, symbol: str) -> np.ndarray:
        """Return candles as numpy float64 array for indicator math.

        Args:
            symbol: Unified symbol.

        Returns:
            np.ndarray of shape (N, 6) with columns [ts, open, high, low, close, volume].
            Empty array if no candles buffered.
        """
        buf = self._buffers.get(symbol)
        if not buf:
            return np.empty((0, 6), dtype=np.float64)
        return np.array(list(buf), dtype=np.float64)

    def as_list(self, symbol: str) -> list[list]:
        """Return candles as plain list of lists.

        Args:
            symbol: Unified symbol.

        Returns:
            List of OHLCV lists, oldest first.
        """
        buf = self._buffers.get(symbol)
        return list(buf) if buf else []

    def has_enough(self, symbol: str, min_candles: int = 50) -> bool:
        """Check if enough candles are buffered for regime classification.

        Args:
            symbol: Unified symbol.
            min_candles: Minimum required (default 50, matching RegimeClassifier floor).

        Returns:
            True if buffer length >= min_candles.
        """
        return len(self._buffers.get(symbol, [])) >= min_candles

    def count(self, symbol: str) -> int:
        """Return number of candles buffered for a symbol."""
        return len(self._buffers.get(symbol, []))

    def clear(self, symbol: str) -> None:
        """Clear all candles for a symbol."""
        self._buffers[symbol].clear()

    def symbols(self) -> list[str]:
        """Return all symbols with buffered data."""
        return list(self._buffers.keys())
