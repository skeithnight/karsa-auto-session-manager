"""Tests for app.consumer.candle_buffer.CandleBuffer."""

from __future__ import annotations

import numpy as np

from app.consumer.candle_buffer import CandleBuffer


class TestCandleBuffer:
    def test_append_and_count(self) -> None:
        buf = CandleBuffer(max_size=200)
        buf.append("BTC/USDT", [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        assert buf.count("BTC/USDT") == 1

    def test_has_enough_returns_false_when_below_min(self) -> None:
        buf = CandleBuffer(max_size=200)
        for i in range(10):
            buf.append("BTC/USDT", [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        assert not buf.has_enough("BTC/USDT", min_candles=50)

    def test_has_enough_returns_true_when_above_min(self) -> None:
        buf = CandleBuffer(max_size=200)
        for i in range(100):
            buf.append("BTC/USDT", [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        assert buf.has_enough("BTC/USDT", min_candles=50)

    def test_max_size_respected(self) -> None:
        buf = CandleBuffer(max_size=10)
        for i in range(20):
            buf.append("BTC/USDT", [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        assert buf.count("BTC/USDT") == 10

    def test_dedup_replaces_duplicate_timestamp(self) -> None:
        buf = CandleBuffer(max_size=200)
        buf.append("BTC/USDT", [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        buf.append("BTC/USDT", [1700000000000, 200.0, 202.0, 199.0, 201.0, 2000.0])
        assert buf.count("BTC/USDT") == 1
        candles = buf.as_list("BTC/USDT")
        assert candles[0][1] == 200.0  # open was replaced

    def test_as_numpy_shape(self) -> None:
        buf = CandleBuffer(max_size=200)
        for i in range(50):
            buf.append("BTC/USDT", [1700000000000 + i * 3600000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        arr = buf.as_numpy("BTC/USDT")
        assert arr.shape == (50, 6)
        assert arr.dtype == np.float64

    def test_as_numpy_empty_for_unknown_symbol(self) -> None:
        buf = CandleBuffer(max_size=200)
        arr = buf.as_numpy("UNKNOWN")
        assert arr.shape == (0, 6)

    def test_as_list_order(self) -> None:
        buf = CandleBuffer(max_size=200)
        for i in range(5):
            buf.append("BTC/USDT", [1700000000000 + i * 3600000, 100.0 + i, 102.0, 99.0, 101.0, 1000.0])
        candles = buf.as_list("BTC/USDT")
        assert len(candles) == 5
        # Oldest first
        assert candles[0][0] < candles[-1][0]

    def test_clear(self) -> None:
        buf = CandleBuffer(max_size=200)
        buf.append("BTC/USDT", [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        buf.clear("BTC/USDT")
        assert buf.count("BTC/USDT") == 0

    def test_symbols(self) -> None:
        buf = CandleBuffer(max_size=200)
        buf.append("BTC/USDT", [1700000000000, 100.0, 102.0, 99.0, 101.0, 1000.0])
        buf.append("ETH/USDT", [1700000000000, 10.0, 11.0, 9.0, 10.5, 5000.0])
        syms = buf.symbols()
        assert "BTC/USDT" in syms
        assert "ETH/USDT" in syms
