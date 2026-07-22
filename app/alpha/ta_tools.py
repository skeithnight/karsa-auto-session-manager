"""Deterministic TA indicators for AI agent context.

Pure math, no network. All accept/return Decimal for financial safety.
Internal float math for speed. Used by analyst.py and position_judge.py.
"""

from __future__ import annotations

from decimal import Decimal
import numpy as np


def calculate_ema(closes: list[Decimal], period: int = 200) -> Decimal | None:
    """Exponential Moving Average."""
    if len(closes) < period:
        return None

    floats = [float(c) for c in closes]
    k = 2 / (period + 1)
    ema = floats[0]
    for price in floats[1:]:
        ema = price * k + ema * (1 - k)
    return Decimal(str(round(ema, 8)))


def calculate_rsi(closes: list[Decimal], period: int = 14) -> Decimal | None:
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
    closes: list[Decimal],
    period: int = 20,
    std_dev_mult: Decimal = Decimal("2"),
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Bollinger Bands: (upper, middle, lower)."""
    if len(closes) < period:
        return None

    window = [float(c) for c in closes[-period:]]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std_dev = variance**0.5

    mult = float(std_dev_mult)
    upper = middle + std_dev * mult
    lower = middle - std_dev * mult

    return (
        Decimal(str(round(upper, 8))),
        Decimal(str(round(middle, 8))),
        Decimal(str(round(lower, 8))),
    )


def calculate_macd(
    closes: list[Decimal],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """MACD: (macd_line, signal_line, histogram)."""
    if len(closes) < slow_period + signal_period:
        return None

    floats = [float(c) for c in closes]

    def _ema(values: list[float], period: int) -> list[float]:
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
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int = 14,
) -> Decimal | None:
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

def calculate_vpvr(arr: np.ndarray, bins: int = 50) -> tuple[float, float, float] | None:
    """Volume Profile Visible Range (VPVR).
    
    Args:
        arr: Numpy array with columns [ts, open, high, low, close, volume]
        bins: Number of price bins
        
    Returns:
        (POC, VAH, VAL) as floats. Returns None if insufficient data.
    """
    if len(arr) < 2:
        return None
        
    highs = arr[:, 2]
    lows = arr[:, 3]
    volumes = arr[:, 5]
    
    min_price = np.min(lows)
    max_price = np.max(highs)
    
    if min_price == max_price:
        return float(min_price), float(min_price), float(min_price)
        
    # Create bin edges
    bin_edges = np.linspace(min_price, max_price, bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    profile = np.zeros(bins)
    
    # Distribute volume proportionally
    for i in range(len(arr)):
        h = highs[i]
        l = lows[i]
        v = volumes[i]
        
        if h == l:
            idx = int(np.searchsorted(bin_edges, l, side='right')) - 1
            idx = np.clip(idx, 0, bins - 1)
            profile[idx] += v
        else:
            start_idx = int(np.searchsorted(bin_edges, l, side='right')) - 1
            end_idx = int(np.searchsorted(bin_edges, h, side='right')) - 1
            start_idx = np.clip(start_idx, 0, bins - 1)
            end_idx = np.clip(end_idx, 0, bins - 1)
            
            if start_idx == end_idx:
                profile[start_idx] += v
            else:
                vol_per_price = v / (h - l)
                for b in range(start_idx, end_idx + 1):
                    bin_low = bin_edges[b]
                    bin_high = bin_edges[b+1]
                    overlap_low = max(l, bin_low)
                    overlap_high = min(h, bin_high)
                    if overlap_high > overlap_low:
                        profile[b] += vol_per_price * (overlap_high - overlap_low)
                        
    # Find POC
    poc_idx = int(np.argmax(profile))
    poc = bin_centers[poc_idx]
    
    # Calculate Value Area (70% of total volume)
    total_vol = np.sum(profile)
    va_vol_target = total_vol * 0.70
    
    va_vol = profile[poc_idx]
    lower_idx = poc_idx
    upper_idx = poc_idx
    
    while va_vol < va_vol_target and (lower_idx > 0 or upper_idx < bins - 1):
        vol_lower = profile[lower_idx - 1] if lower_idx > 0 else -1
        vol_upper = profile[upper_idx + 1] if upper_idx < bins - 1 else -1
        
        if vol_lower > vol_upper:
            lower_idx -= 1
            va_vol += profile[lower_idx]
        elif vol_upper > vol_lower:
            upper_idx += 1
            va_vol += profile[upper_idx]
        else:
            if lower_idx > 0 and upper_idx < bins - 1:
                lower_idx -= 1
                upper_idx += 1
                va_vol += profile[lower_idx] + profile[upper_idx]
            elif lower_idx > 0:
                lower_idx -= 1
                va_vol += profile[lower_idx]
            elif upper_idx < bins - 1:
                upper_idx += 1
                va_vol += profile[upper_idx]
                
    val = bin_centers[lower_idx]
    vah = bin_centers[upper_idx]
    
    return float(poc), float(vah), float(val)
