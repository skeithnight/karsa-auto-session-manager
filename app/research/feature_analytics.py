"""Feature Analytics — compute predictive power of each signal.

Uses mutual information and rank IC to score which features actually
predict next-candle returns. Run offline against historical data.

Usage:
    python -m app.research.feature_analytics --symbol BTC/USDT --days 90
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

from loguru import logger

from app.alpha.ta_tools import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)
from app.core.database import DatabaseEngine
from app.core.config import get_settings


# --- Feature computation (reuses ta_tools) ---

def compute_features(closes: list[Decimal], highs: list[Decimal], lows: list[Decimal]) -> dict[str, list[float]]:
    """Compute all candidate features from OHLC arrays. Returns dict of feature_name -> values[].

    Each feature list is aligned with the close array (same length).
    First N values may be NaN/0 where indicator has insufficient lookback.
    """
    n = len(closes)
    features: dict[str, list[float]] = {}

    # RSI(14)
    rsi_vals = [0.0] * n
    for i in range(14, n):
        r = calculate_rsi(closes[:i + 1], 14)
        rsi_vals[i] = float(r) if r else 50.0
    features["rsi_14"] = rsi_vals

    # Bollinger Band width
    bb_width = [0.0] * n
    for i in range(20, n):
        bb = calculate_bollinger_bands(closes[:i + 1], 20)
        if bb and bb[1] > 0:
            bb_width[i] = float((bb[0] - bb[2]) / bb[1])
    features["bb_width"] = bb_width

    # MACD histogram
    macd_hist = [0.0] * n
    for i in range(26, n):
        m = calculate_macd(closes[:i + 1])
        if m:
            macd_hist[i] = float(m[2])
    features["macd_histogram"] = macd_hist

    # ATR(14) / price (normalized)
    atr_pct = [0.0] * n
    for i in range(14, n):
        a = calculate_atr(highs[:i + 1], lows[:i + 1], closes[:i + 1], 14)
        if a and closes[i] > 0:
            atr_pct[i] = float(a / closes[i])
    features["atr_pct"] = atr_pct

    # Price momentum (1h return)
    momentum_1h = [0.0] * n
    for i in range(1, n):
        if closes[i - 1] > 0:
            momentum_1h[i] = float((closes[i] - closes[i - 1]) / closes[i - 1])
    features["momentum_1h"] = momentum_1h

    # Price momentum (4h return)
    momentum_4h = [0.0] * n
    for i in range(4, n):
        if closes[i - 4] > 0:
            momentum_4h[i] = float((closes[i] - closes[i - 4]) / closes[i - 4])
    features["momentum_4h"] = momentum_4h

    # EMA distance (price vs EMA200)
    ema_dist = [0.0] * n
    for i in range(200, n):
        e = calculate_ema(closes[:i + 1], 200)
        if e and e > 0:
            ema_dist[i] = float((closes[i] - e) / e)
    features["ema_distance"] = ema_dist

    # Volatility (20-period std of returns)
    vol_20 = [0.0] * n
    returns = [0.0] * n
    for i in range(1, n):
        if closes[i - 1] > 0:
            returns[i] = float((closes[i] - closes[i - 1]) / closes[i - 1])
    for i in range(20, n):
        window = returns[i - 19:i + 1]
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        vol_20[i] = math.sqrt(var)
    features["volatility_20"] = vol_20

    return features


def rank_ic(features: dict[str, list[float]], forward_returns: list[float], min_lookback: int = 200) -> dict[str, float]:
    """Compute rank Information Coefficient (Spearman correlation) for each feature.

    Returns dict of feature_name -> IC value. Higher absolute IC = more predictive.
    """
    n = len(forward_returns)
    results = {}

    for name, vals in features.items():
        # Use only valid (non-zero lookback) samples
        pairs = [(vals[i], forward_returns[i]) for i in range(min_lookback, n) if vals[i] != 0.0]
        if len(pairs) < 30:
            results[name] = 0.0
            continue

        # Rank IC via Pearson on ranks
        x_rank = _rank([p[0] for p in pairs])
        y_rank = _rank([p[1] for p in pairs])
        ic = _pearson(x_rank, y_rank)
        results[name] = round(ic, 4)

    return results


def mutual_information(features: dict[str, list[float]], forward_returns: list[float], bins: int = 10, min_lookback: int = 200) -> dict[str, float]:
    """Compute mutual information between each feature and forward returns.

    Higher MI = more predictive power. Returns dict of feature_name -> MI value.
    """
    n = len(forward_returns)
    results = {}

    for name, vals in features.items():
        x = [vals[i] for i in range(min_lookback, n) if vals[i] != 0.0]
        y = [forward_returns[i] for i in range(min_lookback, n) if vals[i] != 0.0]
        if len(x) < 30:
            results[name] = 0.0
            continue
        mi = _mutual_information(x, y, bins)
        results[name] = round(mi, 4)

    return results


def _rank(values: list[float]) -> list[float]:
    """Return fractional ranks of a list."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    for rank, (idx, _) in enumerate(indexed, 1):
        ranks[idx] = float(rank)
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson correlation coefficient."""
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _mutual_information(x: list[float], y: list[float], bins: int) -> float:
    """Discretize and compute MI between two continuous variables."""
    n = len(x)
    # Discretize into bins
    x_min, x_max = min(x), max(x)
    y_min, y_max = min(y), max(y)
    x_range = x_max - x_min or 1.0
    y_range = y_max - y_min or 1.0

    x_bins = [min(int((xi - x_min) / x_range * bins), bins - 1) for xi in x]
    y_bins = [min(int((yi - y_min) / y_range * bins), bins - 1) for yi in y]

    # Count joint and marginal frequencies
    joint = defaultdict(int)
    x_marg = defaultdict(int)
    y_marg = defaultdict(int)
    for xb, yb in zip(x_bins, y_bins):
        joint[(xb, yb)] += 1
        x_marg[xb] += 1
        y_marg[yb] += 1

    # MI = sum P(x,y) * log(P(x,y) / (P(x) * P(y)))
    mi = 0.0
    for (xb, yb), count in joint.items():
        p_xy = count / n
        p_x = x_marg[xb] / n
        p_y = y_marg[yb] / n
        if p_x > 0 and p_y > 0 and p_xy > 0:
            mi += p_xy * math.log2(p_xy / (p_x * p_y))
    return mi


async def load_candles_from_db(symbol: str, days: int = 90) -> tuple[list[Decimal], list[Decimal], list[Decimal], list[Decimal]]:
    """Load historical 1H candles from Postgres. Returns (opens, highs, lows, closes)."""
    settings = get_settings()
    db = DatabaseEngine()
    await db.connect(settings.asyncpg_dsn)

    from sqlalchemy import text
    async with db.engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT open, high, low, close FROM historical_candles "
                "WHERE symbol = :symbol AND timeframe = '1h' "
                "ORDER BY timestamp DESC LIMIT :limit"
            ),
            {"symbol": symbol, "limit": days * 24},
        )
        rows = result.fetchall()

    await db.dispose()

    if not rows:
        return [], [], [], []

    # Reverse to chronological order
    rows = list(reversed(rows))
    opens = [Decimal(str(r[0])) for r in rows]
    highs = [Decimal(str(r[1])) for r in rows]
    lows = [Decimal(str(r[2])) for r in rows]
    closes = [Decimal(str(r[3])) for r in rows]
    return opens, highs, lows, closes


async def analyze_symbol(symbol: str, days: int = 90) -> dict[str, Any]:
    """Full feature analytics for a symbol. Returns ranked features + stats."""
    logger.info(f"Analyzing {symbol} ({days}d of 1H candles)...")

    opens, highs, lows, closes = await load_candles_from_db(symbol, days)
    if len(closes) < 250:
        logger.warning(f"Insufficient data for {symbol}: {len(closes)} candles (need 250+)")
        return {"symbol": symbol, "error": "insufficient_data", "candles": len(closes)}

    logger.info(f"Loaded {len(closes)} candles for {symbol}")

    # Compute features
    features = compute_features(closes, highs, lows)

    # Forward returns (next 1H return)
    forward_returns = [0.0] * len(closes)
    for i in range(len(closes) - 1):
        if closes[i] > 0:
            forward_returns[i] = float((closes[i + 1] - closes[i]) / closes[i])

    # Compute IC and MI
    ic_results = rank_ic(features, forward_returns)
    mi_results = mutual_information(features, forward_returns)

    # Combine and rank
    combined = {}
    for name in features:
        combined[name] = {
            "rank_ic": ic_results.get(name, 0.0),
            "mutual_info": mi_results.get(name, 0.0),
            "abs_ic": abs(ic_results.get(name, 0.0)),
            "score": abs(ic_results.get(name, 0.0)) + mi_results.get(name, 0.0),
        }

    ranked = sorted(combined.items(), key=lambda x: x[1]["score"], reverse=True)

    return {
        "symbol": symbol,
        "candles": len(closes),
        "days": days,
        "features": {name: stats for name, stats in ranked},
        "top_features": [name for name, _ in ranked[:5]],
    }


async def main() -> None:
    """CLI entry point for feature analytics."""
    parser = argparse.ArgumentParser(description="Feature Analytics")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol to analyze")
    parser.add_argument("--days", type=int, default=90, help="Days of history")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    result = await analyze_symbol(args.symbol, args.days)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Results saved to {args.output}")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
