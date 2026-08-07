"""Research CLI — Command-line interface for the Quant CI/CD Platform.

Commands:
    dataset    Manage reproducible datasets
    experiment Run an experiment from a YAML manifest
    compare    Compare two experiments
    leaderboard View experiment rankings
    validate   Run statistical validation (Monte Carlo / Bootstrapping)
    promote    Promote an experiment to Shadow deployment
    ranking    Show strategy ranking decision
    elo        Show ELO rating for a strategy
    elo-all    Show all ELO ratings
    gate       Show dynamic gate calibration
    vol        Show volatility surface
    trades     Show recent closed trades
    metrics    Show trading metrics
"""

import argparse
import asyncio
import json
from loguru import logger


async def _get_redis():
    """Get Redis client."""
    from app.core.dependencies import get_redis, startup
    from app.core.config import get_settings
    settings = get_settings()
    await startup(settings)
    return get_redis()


def cmd_dataset(args: argparse.Namespace) -> None:
    logger.info("Dataset command: %s %s", args.action, args.symbol)

def cmd_experiment(args: argparse.Namespace) -> None:
    logger.info("Running experiment from manifest: %s", args.manifest)

def cmd_compare(args: argparse.Namespace) -> None:
    logger.info("Comparing experiments: %s vs %s", args.exp_a, args.exp_b)

def cmd_leaderboard(args: argparse.Namespace) -> None:
    logger.info("Displaying research leaderboard...")

def cmd_validate(args: argparse.Namespace) -> None:
    logger.info("Validating experiment: %s", args.exp_id)

def cmd_promote(args: argparse.Namespace) -> None:
    logger.info("Promoting experiment %s to Shadow", args.exp_id)


async def _cmd_ranking() -> None:
    """Show strategy ranking decision."""
    redis = await _get_redis()
    decision = await redis.get("karsa:ranking:decision")
    details = await redis.get("karsa:ranking:details")

    print(f"\n{'='*50}")
    print(f"  Strategy Ranking: {decision.decode() if decision else 'N/A'}")
    print(f"{'='*50}")

    if details:
        data = json.loads(details)
        for k, v in data.items():
            if k != "decision":
                print(f"  {k}: {v}")
    print()


async def _cmd_elo(strategy: str | None = None) -> None:
    """Show ELO rating."""
    redis = await _get_redis()
    if strategy:
        raw = await redis.get(f"karsa:elo:{strategy}")
        if raw:
            data = json.loads(raw)
            print(f"\n  ELO: {strategy}")
            print(f"    Rating: {data.get('elo', 1500):.0f}")
            print(f"    Wins: {data.get('wins', 0)}")
            print(f"    Losses: {data.get('losses', 0)}")
            print(f"    Win Rate: {data.get('win_rate', 0)*100:.1f}%")
        else:
            print(f"\n  No ELO data for {strategy}")
    else:
        keys = await redis.keys("karsa:elo:*")
        if not keys:
            print("\n  No ELO ratings yet")
            return
        print(f"\n{'='*60}")
        print(f"  {'Strategy':<25} {'ELO':>6} {'W':>5} {'L':>5} {'WR':>6}")
        print(f"{'='*60}")
        ratings = []
        for key in keys:
            raw = await redis.get(key)
            if raw:
                data = json.loads(raw)
                name = key.decode().replace("karsa:elo:", "")
                ratings.append((name, data.get("elo", 1500), data.get("wins", 0),
                                data.get("losses", 0), data.get("win_rate", 0)))
        ratings.sort(key=lambda x: x[1], reverse=True)
        for name, elo, w, l, wr in ratings:
            print(f"  {name:<25} {elo:>6.0f} {w:>5} {l:>5} {wr*100:>5.1f}%")
    print()


async def _cmd_gate() -> None:
    """Show dynamic gate calibration."""
    redis = await _get_redis()
    raw = await redis.get("karsa:gate:dynamic_threshold")
    if raw:
        data = json.loads(raw)
        print(f"\n  Dynamic Gate Threshold: {data.get('threshold', 75.0):.1f}")
        print(f"  Median EV: {data.get('median_ev', 0):.4f}")
        print(f"  Winning Trades: {data.get('winning_trades', 0)}")
        print(f"  Total Trades: {data.get('total_trades', 0)}")
    else:
        print("\n  No gate calibration data yet (needs 20+ trades)")
    print()


async def _cmd_vol() -> None:
    """Show volatility surface."""
    redis = await _get_redis()
    raw = await redis.get("karsa:vol_surface:composite")
    if raw:
        data = json.loads(raw)
        surface = data.get("surface", {})
        print(f"\n{'='*50}")
        print(f"  Volatility Surface")
        print(f"{'='*50}")
        for asset in ("btc", "eth"):
            if asset in surface:
                print(f"\n  {asset.upper()}:")
                for tf, vol in surface[asset].items():
                    print(f"    {tf}: {vol*100:.1f}%")
        if "spread" in surface:
            print(f"\n  BTC-ETH Spread:")
            for tf, spread in surface["spread"].items():
                print(f"    {tf}: {spread*100:.1f}%")
    else:
        print("\n  No volatility surface data yet")
    print()


async def _cmd_trades() -> None:
    """Show recent closed trades."""
    from app.core.dependencies import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT symbol, direction, pnl_pct, exit_reason, entry_time, exit_time "
            "FROM trades WHERE exit_time IS NOT NULL "
            "ORDER BY exit_time DESC LIMIT 10"
        )
    if not rows:
        print("\n  No closed trades yet")
        return
    print(f"\n{'='*70}")
    print(f"  {'Symbol':<12} {'Dir':<6} {'PnL%':>7} {'Reason':<15} {'Exit Time'}")
    print(f"{'='*70}")
    for r in rows:
        pnl = float(r["pnl_pct"]) * 100
        print(f"  {r['symbol']:<12} {r['direction']:<6} {pnl:>+6.2f}% {r['exit_reason'] or 'N/A':<15} {r['exit_time']}")
    print()


async def _cmd_metrics() -> None:
    """Show trading metrics."""
    from app.core.dependencies import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins, "
            "AVG(pnl_pct) as avg_pnl, "
            "SUM(pnl_pct) as total_pnl "
            "FROM trades WHERE exit_time IS NOT NULL"
        )
    if not row or row["total"] == 0:
        print("\n  No trades to compute metrics")
        return
    total = row["total"]
    wins = row["wins"] or 0
    win_rate = (wins / total * 100) if total > 0 else 0
    avg_pnl = float(row["avg_pnl"] or 0) * 100
    total_pnl = float(row["total_pnl"] or 0) * 100
    print(f"\n{'='*40}")
    print(f"  Trading Metrics")
    print(f"{'='*40}")
    print(f"  Total Trades: {total}")
    print(f"  Wins: {wins}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Avg PnL: {avg_pnl:+.2f}%")
    print(f"  Total PnL: {total_pnl:+.2f}%")
    print()

def main() -> None:
    parser = argparse.ArgumentParser(description="Karsa Research Operating System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # dataset
    p_dataset = subparsers.add_parser("dataset", help="Manage datasets")
    p_dataset.add_argument("action", choices=["download", "list"])
    p_dataset.add_argument("symbol", nargs="?", help="Symbol (e.g. BTCUSDT)")
    p_dataset.set_defaults(func=cmd_dataset)

    # experiment
    p_experiment = subparsers.add_parser("experiment", help="Run experiments")
    p_experiment.add_argument("action", choices=["run"])
    p_experiment.add_argument("manifest", help="Path to YAML manifest")
    p_experiment.set_defaults(func=cmd_experiment)

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare two experiments")
    p_compare.add_argument("exp_a", help="First experiment ID")
    p_compare.add_argument("exp_b", help="Second experiment ID")
    p_compare.set_defaults(func=cmd_compare)

    # leaderboard
    p_leaderboard = subparsers.add_parser("leaderboard", help="View leaderboard")
    p_leaderboard.set_defaults(func=cmd_leaderboard)

    # validate
    p_validate = subparsers.add_parser("validate", help="Statistical validation")
    p_validate.add_argument("exp_id", help="Experiment ID to validate")
    p_validate.set_defaults(func=cmd_validate)

    # promote
    p_promote = subparsers.add_parser("promote", help="Promote to Shadow")
    p_promote.add_argument("exp_id", help="Experiment ID to promote")
    p_promote.set_defaults(func=cmd_promote)

    # ranking
    p_ranking = subparsers.add_parser("ranking", help="Show strategy ranking")
    p_ranking.set_defaults(func=lambda a: asyncio.run(_cmd_ranking()))

    # elo
    p_elo = subparsers.add_parser("elo", help="Show ELO rating")
    p_elo.add_argument("strategy", nargs="?", help="Strategy (e.g. TREND_BULL:LONG)")
    p_elo.set_defaults(func=lambda a: asyncio.run(_cmd_elo(a.strategy)))

    # elo-all
    p_elo_all = subparsers.add_parser("elo-all", help="Show all ELO ratings")
    p_elo_all.set_defaults(func=lambda a: asyncio.run(_cmd_elo()))

    # gate
    p_gate = subparsers.add_parser("gate", help="Show gate calibration")
    p_gate.set_defaults(func=lambda a: asyncio.run(_cmd_gate()))

    # vol
    p_vol = subparsers.add_parser("vol", help="Show volatility surface")
    p_vol.set_defaults(func=lambda a: asyncio.run(_cmd_vol()))

    # trades
    p_trades = subparsers.add_parser("trades", help="Show recent trades")
    p_trades.set_defaults(func=lambda a: asyncio.run(_cmd_trades()))

    # metrics
    p_metrics = subparsers.add_parser("metrics", help="Show trading metrics")
    p_metrics.set_defaults(func=lambda a: asyncio.run(_cmd_metrics()))

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
