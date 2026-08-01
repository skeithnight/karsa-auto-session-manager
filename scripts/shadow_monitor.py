"""Shadow Mode Monitor — tracks paper trading performance.

Usage:
    docker exec karsa-data-engine python scripts/shadow_monitor.py
    # or run locally:
    python scripts/shadow_monitor.py --redis-host localhost --redis-port 6379
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis.asyncio as aioredis


# ── Data Structures ──────────────────────────────────────────


@dataclass
class ShadowTrade:
    """A single shadow trade record."""
    symbol: str
    direction: str
    entry_price: float
    exit_price: float | None = None
    pnl: float = 0.0
    status: str = "open"  # open, closed
    entry_time: str = ""
    exit_time: str = ""
    exit_reason: str = ""
    ai_confidence: float = 0.0
    regime: str = ""
    guardrails_triggered: list[str] = field(default_factory=list)


@dataclass
class ShadowStats:
    """Aggregated shadow trading statistics."""
    total_trades: int = 0
    open_positions: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    symbols_traded: list[str] = field(default_factory=list)
    regime_distribution: dict[str, int] = field(default_factory=dict)
    guardrail_stats: dict[str, int] = field(default_factory=dict)
    ai_confidence_avg: float = 0.0
    hourly_pnl: list[float] = field(default_factory=list)


# ── Monitor ──────────────────────────────────────────────────


class ShadowMonitor:
    """Monitors shadow mode trading performance via Redis."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def get_shadow_trades(self) -> list[ShadowTrade]:
        """Fetch all shadow trades from Redis."""
        trades = []
        # Check shadow position keys
        position_keys = []
        async for key in self._redis.scan_iter("shadow:position:*"):
            position_keys.append(key)

        for key in position_keys:
            raw = await self._redis.get(key)
            if raw:
                try:
                    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    if isinstance(data, dict):
                        trade = ShadowTrade(
                            symbol=data.get("symbol", ""),
                            direction=data.get("direction", ""),
                            entry_price=float(data.get("entry_price", 0)),
                            exit_price=float(data.get("exit_price", 0)) if data.get("exit_price") else None,
                            pnl=float(data.get("pnl", 0)),
                            status=data.get("status", "open"),
                            entry_time=data.get("entry_time", ""),
                            exit_time=data.get("exit_time", ""),
                            exit_reason=data.get("exit_reason", ""),
                            ai_confidence=float(data.get("ai_confidence", 0)),
                            regime=data.get("regime", ""),
                            guardrails_triggered=data.get("guardrails_triggered", []),
                        )
                        trades.append(trade)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        # Also check shadow trade history
        trade_keys = []
        async for key in self._redis.scan_iter("shadow:trade:*"):
            trade_keys.append(key)

        for key in trade_keys:
            raw = await self._redis.get(key)
            if raw:
                try:
                    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    if isinstance(data, dict) and data.get("status") == "closed":
                        trade = ShadowTrade(
                            symbol=data.get("symbol", ""),
                            direction=data.get("direction", ""),
                            entry_price=float(data.get("entry_price", 0)),
                            exit_price=float(data.get("exit_price", 0)) if data.get("exit_price") else None,
                            pnl=float(data.get("pnl", 0)),
                            status="closed",
                            entry_time=data.get("entry_time", ""),
                            exit_time=data.get("exit_time", ""),
                            exit_reason=data.get("exit_reason", ""),
                            ai_confidence=float(data.get("ai_confidence", 0)),
                            regime=data.get("regime", ""),
                            guardrails_triggered=data.get("guardrails_triggered", []),
                        )
                        trades.append(trade)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

        return trades

    async def get_shadow_decisions(self) -> list[dict]:
        """Fetch recent shadow hybrid decisions."""
        decisions = []
        async for key in self._redis.scan_iter("shadow:hybrid_decision:*"):
            raw = await self._redis.get(key)
            if raw:
                try:
                    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    if isinstance(data, dict):
                        decisions.append(data)
                except (json.JSONDecodeError, TypeError):
                    pass
        return decisions

    async def compute_stats(self) -> ShadowStats:
        """Compute comprehensive shadow trading statistics."""
        trades = await self.get_shadow_trades()
        decisions = await self.get_shadow_decisions()

        stats = ShadowStats()

        if not trades:
            stats.total_trades = len(decisions)
            return stats

        stats.total_trades = len(trades)
        stats.open_positions = sum(1 for t in trades if t.status == "open")
        stats.closed_trades = sum(1 for t in trades if t.status == "closed")

        closed = [t for t in trades if t.status == "closed"]
        if closed:
            stats.winning_trades = sum(1 for t in closed if t.pnl > 0)
            stats.losing_trades = sum(1 for t in closed if t.pnl <= 0)
            stats.win_rate = (stats.winning_trades / len(closed)) * 100
            stats.total_pnl = sum(t.pnl for t in closed)
            stats.avg_pnl = stats.total_pnl / len(closed)

            winners = [t.pnl for t in closed if t.pnl > 0]
            losers = [t.pnl for t in closed if t.pnl <= 0]
            stats.avg_winner = sum(winners) / len(winners) if winners else 0
            stats.avg_loser = sum(losers) / len(losers) if losers else 0

            if stats.avg_loser != 0:
                stats.profit_factor = abs(stats.avg_winner * len(winners)) / abs(stats.avg_loser * len(losers))

        # Symbol distribution
        stats.symbols_traded = list(set(t.symbol for t in trades))

        # Regime distribution
        for t in trades:
            if t.regime:
                stats.regime_distribution[t.regime] = stats.regime_distribution.get(t.regime, 0) + 1

        # Guardrail stats
        for t in trades:
            for g in t.guardrails_triggered:
                stats.guardrail_stats[g] = stats.guardrail_stats.get(g, 0) + 1

        # AI confidence
        ai_confidences = [t.ai_confidence for t in trades if t.ai_confidence > 0]
        stats.ai_confidence_avg = sum(ai_confidences) / len(ai_confidences) if ai_confidences else 0

        return stats


# ── Display ──────────────────────────────────────────────────


def display_stats(stats: ShadowStats) -> None:
    """Display shadow statistics in a formatted report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{'='*60}")
    print(f"  SHADOW MODE MONITOR — {now}")
    print(f"{'='*60}")

    print(f"\n📊 Trading Summary:")
    print(f"  Total Trades:      {stats.total_trades}")
    print(f"  Open Positions:    {stats.open_positions}")
    print(f"  Closed Trades:     {stats.closed_trades}")

    if stats.closed_trades > 0:
        print(f"\n💰 Performance:")
        print(f"  Win Rate:          {stats.win_rate:.1f}%")
        print(f"  Winning Trades:    {stats.winning_trades}")
        print(f"  Losing Trades:     {stats.losing_trades}")
        print(f"  Total PnL:         {stats.total_pnl:+.4f}")
        print(f"  Avg PnL:           {stats.avg_pnl:+.4f}")
        print(f"  Avg Winner:        {stats.avg_winner:+.4f}")
        print(f"  Avg Loser:         {stats.avg_loser:+.4f}")
        print(f"  Profit Factor:     {stats.profit_factor:.2f}")

    if stats.symbols_traded:
        print(f"\n📈 Symbols Traded ({len(stats.symbols_traded)}):")
        for sym in sorted(stats.symbols_traded):
            print(f"  - {sym}")

    if stats.regime_distribution:
        print(f"\n🎯 Regime Distribution:")
        for regime, count in sorted(stats.regime_distribution.items(), key=lambda x: -x[1]):
            print(f"  {regime}: {count}")

    if stats.guardrail_stats:
        print(f"\n🛡️ Guardrail Triggers:")
        for guardrail, count in sorted(stats.guardrail_stats.items(), key=lambda x: -x[1]):
            print(f"  {guardrail}: {count}")

    if stats.ai_confidence_avg > 0:
        print(f"\n🤖 AI Confidence:")
        print(f"  Average:           {stats.ai_confidence_avg:.1f}%")

    print(f"\n{'='*60}")


# ── Main ─────────────────────────────────────────────────────


async def main(redis_url: str = "redis://localhost:6379") -> None:
    """Run shadow monitor."""
    redis = aioredis.from_url(redis_url, decode_responses=True)

    try:
        monitor = ShadowMonitor(redis)
        stats = await monitor.compute_stats()
        display_stats(stats)

        # Also dump raw decisions
        decisions = await monitor.get_shadow_decisions()
        if decisions:
            print(f"\n📋 Recent Decisions ({len(decisions)}):")
            for d in decisions[-5:]:  # Last 5
                action = d.get("action", "UNKNOWN")
                symbol = d.get("symbol", "?")
                confidence = d.get("confidence", 0)
                size = d.get("size", "?")
                regime = d.get("regime", "?")
                reasoning = d.get("reasoning", "")[:80]
                print(f"  {symbol}: {action} regime={regime} (size={size}, confidence={confidence})")
                if reasoning:
                    print(f"    reasoning: {reasoning}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shadow Mode Monitor")
    parser.add_argument("--redis-url", default="redis://localhost:6379", help="Redis URL")
    args = parser.parse_args()
    asyncio.run(main(args.redis_url))
