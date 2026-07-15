"""Deterministic TA indicators for AI agent context.

Pure math, no network. All accept/return Decimal for financial safety.
Internal float math for speed. Used by analyst.py and position_judge.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple


def calculate_ema(closes: List[Decimal], period: int = 200) -> Optional[Decimal]:
    """Exponential Moving Average."""
    if len(closes) < period:
        return None

    floats = [float(c) for c in closes]
    k = 2 / (period + 1)
    ema = floats[0]
    for price in floats[1:]:
        ema = price * k + ema * (1 - k)
    return Decimal(str(round(ema, 8)))


def calculate_rsi(closes: List[Decimal], period: int = 14) -> Optional[Decimal]:
    """Relative Strength Index (Wilder's smoothing)."""
    if len(closes) < period + 1:
        return None

    floats = [float(c) for c in closes]
    deltas = [floats[i] - floats[i - 1] for i in range(1, len(floats))]

    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return Decimal(str(round(rsi, 4)))


def calculate_bollinger_bands(
    closes: List[Decimal],
    period: int = 20,
    std_dev_mult: Decimal = Decimal("2"),
) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
    """Bollinger Bands: (upper, middle, lower)."""
    if len(closes) < period:
        return None

    window = [float(c) for c in closes[-period:]]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std_dev = variance ** 0.5

    mult = float(std_dev_mult)
    upper = middle + std_dev * mult
    lower = middle - std_dev * mult

    return (
        Decimal(str(round(upper, 8))),
        Decimal(str(round(middle, 8))),
        Decimal(str(round(lower, 8))),
    )


def calculate_macd(
    closes: List[Decimal],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
    """MACD: (macd_line, signal_line, histogram)."""
    if len(closes) < slow_period + signal_period:
        return None

    floats = [float(c) for c in closes]

    def _ema(values: List[float], period: int) -> List[float]:
        k = 2 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    fast_ema = _ema(floats, fast_period)
    slow_ema = _ema(floats, slow_period)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = _ema(macd_line, signal_period)

    macd_val = macd_line[-1]
    signal_val = signal_line[-1]
    histogram = macd_val - signal_val

    return (
        Decimal(str(round(macd_val, 8))),
        Decimal(str(round(signal_val, 8))),
        Decimal(str(round(histogram, 8))),
    )


def calculate_atr(
    highs: List[Decimal],
    lows: List[Decimal],
    closes: List[Decimal],
    period: int = 14,
) -> Optional[Decimal]:
    """Average True Range (Wilder's smoothing)."""
    if len(closes) < period + 1:
        return None

    h = [float(x) for x in highs]
    l = [float(x) for x in lows]
    c = [float(x) for x in closes]

    trs = []
    for i in range(1, len(c)):
        tr = max(
            h[i] - l[i],
            abs(h[i] - c[i - 1]),
            abs(l[i] - c[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return Decimal(str(round(atr, 8)))
