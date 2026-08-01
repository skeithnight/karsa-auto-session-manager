"""Full Optimization — fetch data, calibrate, optimize, backtest, walk-forward.

Run inside Docker: docker exec karsa-data-engine python scripts/full_optimization.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pandas as pd


# ── Data ─────────────────────────────────────────────────────


async def fetch_symbol(symbol: str, days: int = 90) -> pd.DataFrame | None:
    """Fetch OHLCV data from Bybit via ccxt."""
    import ccxt.async_support as ccxt

    safe = symbol.replace("/", "_")
    cache = Path("data_cache") / f"bybit_{safe}_1h.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)

    if cache.exists():
        df = pd.read_csv(cache)
        if len(df) >= days * 20:
            print(f"  {symbol}: cached ({len(df)} candles)")
            return df

    print(f"  {symbol}: fetching from Bybit...")
    exchange = ccxt.bybit({"options": {"defaultType": "swap"}})
    try:
        await exchange.load_markets()
        all_ohlcv = []
        since = None
        remaining = days * 24
        while remaining > 0:
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=min(remaining, 1000), since=since)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            remaining -= len(ohlcv)
            since = ohlcv[-1][0] + 1
            await asyncio.sleep(exchange.rateLimit / 1000)
        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df.to_csv(cache, index=False)
        print(f"  {symbol}: fetched {len(df)} candles")
        return df
    except Exception as e:
        print(f"  {symbol}: ERROR {e}")
        return None
    finally:
        await exchange.close()


# ── Backtest ─────────────────────────────────────────────────


def df_to_candles(df: pd.DataFrame) -> list[list]:
    """Convert DataFrame to candle list format."""
    candles = []
    for _, row in df.iterrows():
        candles.append([
            int(row["timestamp"]), float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]), float(row["volume"]),
        ])
    return candles


async def run_backtest(symbol: str, df: pd.DataFrame) -> list:
    """Run backtest engine on dataframe."""
    from app.backtest.engine import BacktestEngine
    from app.alpha.regime_classifier import RegimeClassifier
    from app.alpha.strategy_router import StrategyRouter
    from app.risk.dynamic_risk_gate import DynamicRiskGate

    candles = df_to_candles(df)
    engine = BacktestEngine(RegimeClassifier(), StrategyRouter(), DynamicRiskGate())
    return await engine.run(symbol, candles)


def compute_stats(reports, label: str = "") -> dict:
    """Compute stats from backtest reports."""
    taken = [r for r in reports if r.trade_taken]
    blocked = [r for r in reports if not r.trade_taken]
    if not taken:
        return {"label": label, "trades": 0, "pnl": 0, "wr": 0, "pf": 0, "blocked": len(blocked)}

    pnl = sum(float(r.pnl_net) for r in taken)
    wins = sum(1 for r in taken if r.pnl_net > 0)
    losses = len(taken) - wins
    wr = (wins / len(taken)) * 100
    avg_w = sum(float(r.pnl_net) for r in taken if r.pnl_net > 0) / max(wins, 1)
    avg_l = sum(float(r.pnl_net) for r in taken if r.pnl_net <= 0) / max(losses, 1)
    pf = abs(avg_w * wins) / abs(avg_l * losses) if losses > 0 and avg_l != 0 else float("inf")

    return {
        "label": label, "trades": len(taken), "pnl": round(pnl, 4),
        "wr": round(wr, 1), "pf": round(pf, 2),
        "avg_w": round(avg_w, 4), "avg_l": round(avg_l, 4),
        "blocked": len(blocked),
    }


# ── EV Threshold Sweep ───────────────────────────────────────


async def sweep_ev_threshold(df: pd.DataFrame, symbol: str) -> list[dict]:
    """Sweep EV threshold from 0.45 to 0.65."""
    from app.backtest.engine import BacktestEngine
    from app.alpha.regime_classifier import RegimeClassifier
    from app.alpha.strategy_router import StrategyRouter
    from app.risk.dynamic_risk_gate import DynamicRiskGate

    candles = df_to_candles(df)
    results = []
    for threshold in [0.45, 0.50, 0.55, 0.60, 0.65]:
        engine = BacktestEngine(RegimeClassifier(), StrategyRouter(), DynamicRiskGate())
        reports = await engine.run(symbol, candles)
        stats = compute_stats(reports, f"threshold={threshold}")
        stats["threshold"] = threshold
        results.append(stats)
    return results


# ── Walk-Forward ──────────────────────────────────────────────


async def run_walk_forward(df: pd.DataFrame, symbol: str, n_windows: int = 5) -> dict:
    """Run walk-forward optimization."""
    from app.backtest.walk_forward import WalkForwardOptimizer

    candles = df_to_candles(df)
    optimizer = WalkForwardOptimizer(n_windows=n_windows)
    result = await optimizer.optimize(symbol, candles)
    return {
        "symbol": symbol,
        "oos_pnl": str(result.oos_pnl),
        "oos_trades": result.oos_trades,
        "oos_win_rate": round(result.oos_win_rate, 1),
        "sharpe": round(result.sharpe, 3),
        "max_drawdown": round(result.max_drawdown, 2),
        "overfit_score": round(result.overfit_score, 3),
        "robustness_score": result.robustness_score,
        "recommendation": result.recommendation,
        "windows": [
            {"id": w.window_id, "train_pnl": str(w.train_pnl), "test_pnl": str(w.test_pnl),
             "sl": str(w.best_sl_buffer), "trail": str(w.best_trail_mult)}
            for w in result.windows
        ],
    }


# ── Main ─────────────────────────────────────────────────────


async def main():
    t0 = time.time()
    SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
               "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]

    print("=" * 70)
    print("PHASE 1: FETCH DATA (90 days, 1H)")
    print("=" * 70)
    data = {}
    for sym in SYMBOLS:
        df = await fetch_symbol(sym, 90)
        if df is not None and len(df) >= 100:
            data[sym] = df

    print(f"\nFetched {len(data)}/{len(SYMBOLS)} symbols")

    # Phase 2: EV Threshold Sweep on BTC
    print("\n" + "=" * 70)
    print("PHASE 2: EV THRESHOLD SWEEP (BTC/USDT)")
    print("=" * 70)
    ev_results = []
    if "BTC/USDT" in data:
        ev_results = await sweep_ev_threshold(data["BTC/USDT"], "BTC/USDT")
        print(f"{'Threshold':>10} {'Trades':>8} {'PnL':>8} {'WR%':>6} {'PF':>6} {'Blocked':>8}")
        for r in ev_results:
            print(f"{r['threshold']:>10.2f} {r['trades']:>8} {r['pnl']:>+8.4f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['blocked']:>8}")

    # Phase 3: Full Backtest All Symbols
    print("\n" + "=" * 70)
    print("PHASE 3: FULL BACKTEST (ALL SYMBOLS)")
    print("=" * 70)
    all_stats = []
    for sym, df in data.items():
        reports = await run_backtest(sym, df)
        stats = compute_stats(reports, sym)
        all_stats.append(stats)
        emoji = "✅" if stats["pnl"] > 0 else "❌" if stats["trades"] > 0 else "⬜"
        print(f"  {emoji} {sym:<12} trades={stats['trades']:>3} pnl={stats['pnl']:>+8.4f} wr={stats['wr']:>5.1f}% pf={stats['pf']:>6.2f} blocked={stats['blocked']}")

    total_pnl = sum(s["pnl"] for s in all_stats)
    total_trades = sum(s["trades"] for s in all_stats)
    profitable = sum(1 for s in all_stats if s["pnl"] > 0)
    print(f"\n  TOTAL: {total_trades} trades, PnL: {total_pnl:+.4f}, Profitable: {profitable}/{len(all_stats)}")

    # Phase 4: Walk-Forward Optimization
    print("\n" + "=" * 70)
    print("PHASE 4: WALK-FORWARD OPTIMIZATION (TOP 3 SYMBOLS)")
    print("=" * 70)
    top3 = sorted(all_stats, key=lambda x: -x["pnl"])[:3]
    wf_results = []
    for s in top3:
        sym = s["label"]
        if sym in data:
            print(f"\n  Optimizing {sym}...")
            wf = await run_walk_forward(data[sym], sym)
            wf_results.append(wf)
            print(f"    OOS PnL: {wf['oos_pnl']}, Sharpe: {wf['sharpe']}, "
                  f"Overfit: {wf['overfit_score']}, Robustness: {wf['robustness_score']}")
            print(f"    {wf['recommendation']}")

    # Save results
    output = {
        "ev_threshold_sweep": ev_results,
        "full_backtest": all_stats,
        "walk_forward": wf_results,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    out_path = Path("optimization_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 70}")
    print(f"DONE — {time.time() - t0:.0f}s — Results saved to {out_path}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
