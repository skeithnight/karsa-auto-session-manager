"""Tests for TA Tools — deterministic indicators."""

from decimal import Decimal

from app.alpha.ta_tools import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)


class TestRSI:
    def test_overbought(self):
        closes = [Decimal(str(100 + i * 2)) for i in range(30)]
        rsi = calculate_rsi(closes, 14)
        assert rsi is not None
        assert rsi > 70

    def test_oversold(self):
        closes = [Decimal(str(100 - i * 2)) for i in range(30)]
        rsi = calculate_rsi(closes, 14)
        assert rsi is not None
        assert rsi < 30

    def test_neutral(self):
        closes = [Decimal(str(100 + (i % 2) * 2 - 1)) for i in range(30)]
        rsi = calculate_rsi(closes, 14)
        assert rsi is not None
        assert 30 <= rsi <= 70

    def test_insufficient_data(self):
        closes = [Decimal("100")] * 5
        assert calculate_rsi(closes, 14) is None

    def test_decimal_return(self):
        closes = [Decimal(str(100 + i)) for i in range(30)]
        rsi = calculate_rsi(closes, 14)
        assert isinstance(rsi, Decimal)


class TestBollingerBands:
    def test_bands_width(self):
        closes = [Decimal(str(100 + (i % 5))) for i in range(30)]
        result = calculate_bollinger_bands(closes, 20)
        assert result is not None
        upper, mid, lower = result
        assert upper > mid > lower

    def test_insufficient_data(self):
        closes = [Decimal("100")] * 5
        assert calculate_bollinger_bands(closes, 20) is None

    def test_decimal_return(self):
        closes = [Decimal(str(100 + i % 3)) for i in range(30)]
        result = calculate_bollinger_bands(closes, 20)
        assert all(isinstance(x, Decimal) for x in result)


class TestMACD:
    def test_crossover(self):
        closes = [Decimal(str(100 + i * 0.5)) for i in range(50)]
        result = calculate_macd(closes)
        assert result is not None
        macd_line, signal_line, hist = result
        assert macd_line > signal_line

    def test_insufficient_data(self):
        closes = [Decimal("100")] * 10
        assert calculate_macd(closes) is None

    def test_decimal_return(self):
        closes = [Decimal(str(100 + i * 0.3)) for i in range(50)]
        result = calculate_macd(closes)
        assert all(isinstance(x, Decimal) for x in result)


class TestATR:
    def test_positive(self):
        n = 30
        highs = [Decimal(str(105 + i)) for i in range(n)]
        lows = [Decimal(str(95 + i)) for i in range(n)]
        closes = [Decimal(str(100 + i)) for i in range(n)]
        atr = calculate_atr(highs, lows, closes, 14)
        assert atr is not None
        assert atr > 0

    def test_insufficient_data(self):
        highs = [Decimal("105")] * 5
        lows = [Decimal("95")] * 5
        closes = [Decimal("100")] * 5
        assert calculate_atr(highs, lows, closes, 14) is None

    def test_decimal_return(self):
        n = 30
        highs = [Decimal(str(105 + i)) for i in range(n)]
        lows = [Decimal(str(95 + i)) for i in range(n)]
        closes = [Decimal(str(100 + i)) for i in range(n)]
        atr = calculate_atr(highs, lows, closes, 14)
        assert isinstance(atr, Decimal)


class TestEMA:
    def test_flat(self):
        closes = [Decimal("100")] * 200
        ema = calculate_ema(closes, 200)
        assert ema is not None
        assert abs(ema - Decimal("100")) < Decimal("0.01")

    def test_uptrend(self):
        closes = [Decimal(str(100 + i)) for i in range(200)]
        ema = calculate_ema(closes, 200)
        assert ema is not None
        assert ema > Decimal("100")

    def test_insufficient_data(self):
        closes = [Decimal("100")] * 5
        assert calculate_ema(closes, 200) is None

    def test_decimal_return(self):
        closes = [Decimal(str(100 + i)) for i in range(200)]
        ema = calculate_ema(closes, 200)
        assert isinstance(ema, Decimal)
