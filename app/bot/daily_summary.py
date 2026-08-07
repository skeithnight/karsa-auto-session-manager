"""Daily Summary Service — generates and sends daily trading summary to Telegram.

Fetches today's trades from PostgreSQL, computes PnL/win rate/AI performance,
and formats a comprehensive summary message. Respects user's daily_summary
alert preference (karsa:settings:alerts:daily_summary).

Callers: live_loop.py (background loop), bot handlers (manual /summary command).
Data sources: trades table, ai_decisions table, Redis keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:
    from app.core.database import DatabaseEngine
    from app.core.redis_client import RedisClient


class DailySummaryService:
    """Generate and send daily trading summary to Telegram."""

    def __init__(
        self,
        redis_client: RedisClient,
        db_engine: DatabaseEngine,
    ) -> None:
        self.redis = redis_client
        self.db = db_engine

    async def is_enabled(self) -> bool:
        """Check if daily summary alerts are enabled in user settings."""
        try:
            raw = await self.redis.get("karsa:settings:alerts:daily_summary")
            if raw is None:
                return True  # default: enabled
            return raw in ("1", b"1")
        except Exception:
            return True

    async def generate_summary(self, target_date: datetime | None = None) -> str:
        """Generate the daily summary message for the given date (default: today UTC).

        Returns formatted HTML string ready for Telegram.
        """
        now = datetime.now(timezone.utc)
        if target_date is None:
            # If called during 00:00-01:00 UTC (first hour of new day), summarize completed yesterday
            if now.hour == 0:
                target_date = now - timedelta(days=1)
            else:
                target_date = now

        date_str = target_date.strftime("%Y-%m-%d")
        day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        # Fetch today's closed trades
        today_trades = await self._fetch_trades_between(day_start, day_end)

        # Fetch week and month PnL
        week_start = day_start - timedelta(days=day_start.weekday())  # Monday
        month_start = day_start.replace(day=1)
        total_start = datetime(2000, 1, 1, tzinfo=timezone.utc)  # all-time

        week_pnl, week_pct = await self._fetch_pnl_summary(week_start, day_end)
        month_pnl, month_pct = await self._fetch_pnl_summary(month_start, day_end)
        total_pnl, total_pct = await self._fetch_pnl_summary(total_start, day_end)

        # Today's stats
        today_pnl = sum(Decimal(str(t.get("pnl", 0) or 0)) for t in today_trades)
        today_pct = await self._compute_pnl_pct(today_pnl, day_start, day_end)

        wins = [t for t in today_trades if Decimal(str(t.get("pnl", 0) or 0)) > 0]
        total_trades = len(today_trades)
        win_count = len(wins)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

        # Average trade duration
        avg_duration = self._compute_avg_duration(today_trades)

        # Best trade
        best_trade = max(today_trades, key=lambda t: Decimal(str(t.get("pnl", 0) or 0)), default=None)

        # AI performance
        ai_stats = await self._fetch_ai_stats(day_start, day_end)

        # Guardrail stats
        guardrail_stats = await self._fetch_guardrail_stats(day_start, day_end)

        # Signals generated today
        signals_count = await self._count_signals(day_start, day_end)

        # Top/worst performers by symbol
        top_performers, worst_performers = self._rank_performers(today_trades)

        # Format message
        return self._format_message(
            date_str=date_str,
            today_pnl=today_pnl,
            today_pct=today_pct,
            week_pnl=week_pnl,
            week_pct=week_pct,
            month_pnl=month_pnl,
            month_pct=month_pct,
            total_pnl=total_pnl,
            total_pct=total_pct,
            signals_count=signals_count,
            total_trades=total_trades,
            win_count=win_count,
            win_rate=win_rate,
            avg_duration=avg_duration,
            best_trade=best_trade,
            ai_stats=ai_stats,
            guardrail_stats=guardrail_stats,
            top_performers=top_performers,
            worst_performers=worst_performers,
        )

    # ── Data Fetching ──────────────────────────────────────────────────

    async def _fetch_trades_between(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Fetch closed trades within a time range (filtered by exit_time)."""
        for table in ("trades", "shadow_trades"):
            try:
                async with self.db.engine.connect() as conn:
                    rows = await conn.execute(
                        text(
                            f"""SELECT symbol, side, amount, entry_price, exit_price,
                            pnl, regime, entry_time, exit_time, exit_reason, ai_confidence
                            FROM {table}
                            WHERE exit_time IS NOT NULL
                            AND exit_time >= :start AND exit_time < :end
                            ORDER BY exit_time DESC"""
                        ),
                        {"start": start, "end": end},
                    )
                    trades = [
                        {
                            "symbol": r[0],
                            "side": r[1],
                            "amount": r[2],
                            "entry_price": r[3],
                            "exit_price": r[4],
                            "pnl": r[5],
                            "regime": r[6],
                            "entry_time": r[7],
                            "exit_time": r[8],
                            "exit_reason": r[9],
                            "ai_confidence": r[10],
                        }
                        for r in rows.fetchall()
                    ]
                    if trades:
                        return trades
            except Exception as exc:
                logger.error(f"daily_summary_fetch_trades_failed_{table}: {exc}")
        return []

    async def _fetch_pnl_summary(
        self, start: datetime, end: datetime
    ) -> tuple[Decimal, float]:
        """Fetch aggregate PnL and percentage for a time range (filtered by exit_time)."""
        for table in ("trades", "shadow_trades"):
            try:
                async with self.db.engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            f"""SELECT COALESCE(SUM(pnl), 0)
                            FROM {table}
                            WHERE exit_time IS NOT NULL
                            AND exit_time >= :start AND exit_time < :end"""
                        ),
                        {"start": start, "end": end},
                    )
                    row = result.fetchone()
                    total_pnl = Decimal(str(row[0])) if row and row[0] else Decimal("0")

                    # Compute percentage from entry values
                    pct_result = await conn.execute(
                        text(
                            f"""SELECT COALESCE(SUM(entry_price * amount), 0)
                            FROM {table}
                            WHERE exit_time IS NOT NULL
                            AND exit_time >= :start AND exit_time < :end"""
                        ),
                        {"start": start, "end": end},
                    )
                    pct_row = pct_result.fetchone()
                    total_exposure = Decimal(str(pct_row[0])) if pct_row and pct_row[0] else Decimal("0")
                    pct = float(total_pnl / total_exposure * 100) if total_exposure > 0 else 0.0

                    if total_pnl != Decimal("0") or total_exposure != Decimal("0"):
                        return total_pnl, pct
            except Exception as exc:
                logger.error(f"daily_summary_fetch_pnl_failed_{table}: {exc}")
        return Decimal("0"), 0.0

    async def _compute_pnl_pct(
        self, pnl: Decimal, start: datetime, end: datetime | None = None
    ) -> float:
        """Compute PnL percentage relative to exposure in the period."""
        end_dt = end or (start + timedelta(days=1))
        for table in ("trades", "shadow_trades"):
            try:
                async with self.db.engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            f"""SELECT COALESCE(SUM(entry_price * amount), 0)
                            FROM {table}
                            WHERE exit_time IS NOT NULL
                            AND exit_time >= :start AND exit_time < :end_dt"""
                        ),
                        {"start": start, "end_dt": end_dt},
                    )
                    row = result.fetchone()
                    exposure = Decimal(str(row[0])) if row and row[0] else Decimal("0")
                    if exposure > 0:
                        return float(pnl / exposure * 100)
            except Exception:
                pass
        return 0.0

    async def _count_signals(self, start: datetime, end: datetime) -> int:
        """Count signals generated in the time range."""
        try:
            async with self.db.engine.connect() as conn:
                result = await conn.execute(
                    text(
                        """SELECT COUNT(*) FROM signals
                        WHERE timestamp >= :start AND timestamp < :end"""
                    ),
                    {"start": start, "end": end},
                )
                row = result.fetchone()
                return row[0] if row else 0
        except Exception as exc:
            logger.error("daily_summary_count_signals_failed: {}", exc)
            return 0

    async def _fetch_ai_stats(
        self, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Fetch AI performance stats for the day."""
        stats = {
            "evaluations": 0,
            "avg_confidence": 0,
            "high_conf_wr": 0.0,
            "contribution": Decimal("0"),
            "cost": 0.0,
        }
        try:
            async with self.db.engine.connect() as conn:
                # 1. AI evaluations count from ai_decisions table
                try:
                    res = await conn.execute(
                        text(
                            """SELECT COUNT(*) FROM ai_decisions
                            WHERE created_at >= :start AND created_at < :end"""
                        ),
                        {"start": start, "end": end},
                    )
                    row = res.fetchone()
                    if row and row[0]:
                        stats["evaluations"] = row[0]
                except Exception:
                    pass

                # If ai_decisions was empty, check signals table
                if stats["evaluations"] == 0:
                    try:
                        sig_res = await conn.execute(
                            text(
                                """SELECT COUNT(*) FROM signals
                                WHERE ai_confidence_score IS NOT NULL
                                AND timestamp >= :start AND timestamp < :end"""
                            ),
                            {"start": start, "end": end},
                        )
                        sig_row = sig_res.fetchone()
                        if sig_row and sig_row[0]:
                            stats["evaluations"] = sig_row[0]
                    except Exception:
                        pass

                # 2. Confidence / Win Rate / Contribution from closed trades (trades or shadow_trades)
                for table in ("trades", "shadow_trades"):
                    t_res = await conn.execute(
                        text(
                            f"""SELECT COUNT(*), COALESCE(AVG(ai_confidence), 0)
                            FROM {table}
                            WHERE exit_time IS NOT NULL
                            AND ai_confidence IS NOT NULL
                            AND exit_time >= :start AND exit_time < :end"""
                        ),
                        {"start": start, "end": end},
                    )
                    t_row = t_res.fetchone()
                    if t_row and t_row[0] and t_row[0] > 0:
                        if stats["evaluations"] == 0:
                            stats["evaluations"] = t_row[0]
                        stats["avg_confidence"] = int(t_row[1])

                        # High confidence win rate (ai_confidence > 80)
                        hc_result = await conn.execute(
                            text(
                                f"""SELECT
                                    COUNT(*) FILTER (WHERE pnl > 0) as wins,
                                    COUNT(*) as total
                                FROM {table}
                                WHERE exit_time IS NOT NULL
                                AND ai_confidence > 80
                                AND exit_time >= :start AND exit_time < :end"""
                            ),
                            {"start": start, "end": end},
                        )
                        hc_row = hc_result.fetchone()
                        if hc_row and hc_row[1] and hc_row[1] > 0:
                            stats["high_conf_wr"] = (hc_row[0] / hc_row[1]) * 100

                        # AI contribution: PnL from AI-assisted trades
                        contrib_result = await conn.execute(
                            text(
                                f"""SELECT COALESCE(SUM(pnl), 0)
                                FROM {table}
                                WHERE exit_time IS NOT NULL
                                AND ai_confidence IS NOT NULL AND ai_confidence > 50
                                AND exit_time >= :start AND exit_time < :end"""
                            ),
                            {"start": start, "end": end},
                        )
                        contrib_row = contrib_result.fetchone()
                        if contrib_row and contrib_row[0]:
                            stats["contribution"] = Decimal(str(contrib_row[0]))
                        break

                # If avg_confidence is still 0, check signals table
                if stats["avg_confidence"] == 0:
                    try:
                        sig_avg_res = await conn.execute(
                            text(
                                """SELECT COALESCE(AVG(ai_confidence_score), 0)
                                FROM signals
                                WHERE ai_confidence_score IS NOT NULL
                                AND timestamp >= :start AND timestamp < :end"""
                            ),
                            {"start": start, "end": end},
                        )
                        sig_avg_row = sig_avg_res.fetchone()
                        if sig_avg_row and sig_avg_row[0]:
                            stats["avg_confidence"] = int(sig_avg_row[0])
                    except Exception:
                        pass

            # API cost from Redis
            date_str = start.strftime("%Y-%m-%d")
            cost_raw = await self.redis.get(f"karsa:ai:cost:{date_str}")
            if cost_raw:
                stats["cost"] = float(cost_raw)

        except Exception as exc:
            logger.debug("daily_summary_ai_stats_partial: {}", exc)
        return stats

    async def _fetch_guardrail_stats(
        self, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Fetch guardrail trigger stats for the day."""
        stats = {
            "hard_triggered": 0,
            "trades_blocked": 0,
            "estimated_savings": Decimal("0"),
            "soft_triggered": 0,
            "downgrades": 0,
        }
        try:
            # Read from Redis counters (incremented by guardrail components)
            date_str = start.strftime("%Y-%m-%d")
            prefix = f"karsa:guardrails:stats:{date_str}"

            for key, field in [
                (f"{prefix}:hard_triggered", "hard_triggered"),
                (f"{prefix}:trades_blocked", "trades_blocked"),
                (f"{prefix}:soft_triggered", "soft_triggered"),
                (f"{prefix}:downgrades", "downgrades"),
            ]:
                raw = await self.redis.get(key)
                if raw:
                    stats[field] = int(raw)

            savings_raw = await self.redis.get(f"{prefix}:estimated_savings")
            if savings_raw:
                stats["estimated_savings"] = Decimal(str(savings_raw))

            # Supplement from signals table (signals blocked by risk gate)
            async with self.db.engine.connect() as conn:
                try:
                    res = await conn.execute(
                        text(
                            """SELECT COUNT(*) FROM signals
                            WHERE risk_passed = FALSE
                            AND timestamp >= :start AND timestamp < :end"""
                        ),
                        {"start": start, "end": end},
                    )
                    row = res.fetchone()
                    if row and row[0]:
                        db_blocked = row[0]
                        stats["trades_blocked"] = max(stats["trades_blocked"], db_blocked)
                        stats["hard_triggered"] = max(stats["hard_triggered"], db_blocked)
                except Exception:
                    pass

        except Exception as exc:
            logger.debug("daily_summary_guardrail_stats_partial: {}", exc)
        return stats

    # ── Computation Helpers ────────────────────────────────────────────

    def _compute_avg_duration(self, trades: list[dict]) -> str:
        """Compute average trade duration as human-readable string."""
        durations = []
        for t in trades:
            entry = t.get("entry_time")
            exit_t = t.get("exit_time")
            if entry and exit_t:
                try:
                    if isinstance(entry, str):
                        entry = datetime.fromisoformat(entry)
                    if isinstance(exit_t, str):
                        exit_t = datetime.fromisoformat(exit_t)
                    diff = exit_t - entry
                    durations.append(diff.total_seconds())
                except Exception:
                    pass

        if not durations:
            return "N/A"

        avg_seconds = sum(durations) / len(durations)
        hours = int(avg_seconds // 3600)
        minutes = int((avg_seconds % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def _rank_performers(
        self, trades: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Rank trades by PnL to find top and worst performers."""
        symbol_pnl: dict[str, Decimal] = {}
        symbol_pct: dict[str, float] = {}

        for t in trades:
            symbol = t.get("symbol", "?")
            pnl = Decimal(str(t.get("pnl", 0) or 0))
            entry_price = Decimal(str(t.get("entry_price", 0) or 0))
            amount = Decimal(str(t.get("amount", 0) or 0))

            symbol_pnl[symbol] = symbol_pnl.get(symbol, Decimal("0")) + pnl
            if entry_price > 0 and amount > 0:
                exposure = entry_price * amount
                pct = float(pnl / exposure * 100)
                symbol_pct[symbol] = symbol_pct.get(symbol, 0.0) + pct

        # Sort by PnL
        sorted_symbols = sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)

        top = [
            {"symbol": s, "pnl": p, "pct": symbol_pct.get(s, 0.0)}
            for s, p in sorted_symbols
            if p > 0
        ][:3]
        worst = [
            {"symbol": s, "pnl": p, "pct": symbol_pct.get(s, 0.0)}
            for s, p in reversed(sorted_symbols)
            if p < 0
        ][:3]

        return top, worst

    # ── Formatting ─────────────────────────────────────────────────────

    def _format_message(
        self,
        *,
        date_str: str,
        today_pnl: Decimal,
        today_pct: float,
        week_pnl: Decimal,
        week_pct: float,
        month_pnl: Decimal,
        month_pct: float,
        total_pnl: Decimal,
        total_pct: float,
        signals_count: int,
        total_trades: int,
        win_count: int,
        win_rate: float,
        avg_duration: str,
        best_trade: dict | None,
        ai_stats: dict,
        guardrail_stats: dict,
        top_performers: list[dict],
        worst_performers: list[dict],
    ) -> str:
        """Format the daily summary as HTML for Telegram."""
        lines = []
        lines.append(f"\U0001f4ca <b>Daily Summary — {date_str}</b>")
        lines.append("━" * 36)

        # PnL section
        lines.append("")
        lines.append("\U0001f4b0 <b>PnL</b>")
        lines.append(f"├─ Today's PnL: {self._fmt_pnl(today_pnl)} ({self._fmt_pct(today_pct)})")
        lines.append(f"├─ Week PnL: {self._fmt_pnl(week_pnl)} ({self._fmt_pct(week_pct)})")
        lines.append(f"├─ Month PnL: {self._fmt_pnl(month_pnl)} ({self._fmt_pct(month_pct)})")
        lines.append(f"└─ Total PnL: {self._fmt_pnl(total_pnl)} ({self._fmt_pct(total_pct)})")

        # Trading Activity
        lines.append("")
        lines.append("\U0001f4c8 <b>Trading Activity</b>")
        lines.append(f"├─ Signals Generated: {signals_count}")
        lines.append(f"├─ Trades Executed: {total_trades}")
        lines.append(f"├─ Win Rate: {win_rate:.1f}% ({win_count}/{total_trades})")
        lines.append(f"├─ Avg Duration: {avg_duration}")
        if best_trade:
            bt_pnl = Decimal(str(best_trade.get("pnl", 0) or 0))
            lines.append(f"└─ Best Trade: {best_trade.get('symbol', '?')} {self._fmt_pnl(bt_pnl)}")
        else:
            lines.append("└─ Best Trade: N/A")

        # AI Performance
        lines.append("")
        lines.append("\U0001f9e0 <b>AI Performance</b>")
        lines.append(f"├─ Evaluations: {ai_stats.get('evaluations', 0)}")
        lines.append(f"├─ Avg Confidence: {ai_stats.get('avg_confidence', 0)}/100")
        lines.append(f"├─ High Confidence Win Rate: {ai_stats.get('high_conf_wr', 0):.0f}%")
        ai_contrib = ai_stats.get("contribution", Decimal("0"))
        lines.append(f"├─ AI Contribution: {self._fmt_pnl(ai_contrib)}")
        lines.append(f"└─ API Cost: ${ai_stats.get('cost', 0.0):.2f}")

        # Guardrails
        lines.append("")
        lines.append("\U0001f6e1️ <b>Guardrails</b>")
        lines.append(f"├─ Hard Triggered: {guardrail_stats.get('hard_triggered', 0)}")
        lines.append(f"├─ Trades Blocked: {guardrail_stats.get('trades_blocked', 0)}")
        savings = guardrail_stats.get("estimated_savings", Decimal("0"))
        lines.append(f"├─ Estimated Savings: {self._fmt_pnl(savings)}")
        lines.append(f"├─ Soft Triggered: {guardrail_stats.get('soft_triggered', 0)}")
        lines.append(f"└─ Downgrades: {guardrail_stats.get('downgrades', 0)}")

        # Top Performers
        if top_performers:
            lines.append("")
            lines.append("\U0001f4ca <b>Top Performers</b>")
            for i, p in enumerate(top_performers, 1):
                prefix = "├─" if i < len(top_performers) else "└─"
                lines.append(f"{prefix} {i}. {p['symbol']}: {self._fmt_pnl(Decimal(str(p['pnl'])))} ({self._fmt_pct(p['pct'])})")

        # Worst Performers
        if worst_performers:
            lines.append("")
            lines.append("\U0001f4c9 <b>Worst Performers</b>")
            for i, p in enumerate(worst_performers, 1):
                prefix = "├─" if i < len(worst_performers) else "└─"
                lines.append(f"{prefix} {i}. {p['symbol']}: {self._fmt_pnl(Decimal(str(p['pnl'])))} ({self._fmt_pct(p['pct'])})")

        lines.append("")
        lines.append("━" * 36)

        return "\n".join(lines)

    @staticmethod
    def _fmt_pnl(pnl: Decimal) -> str:
        """Format PnL with sign and dollar symbol."""
        val = float(pnl)
        if val >= 0:
            return f"+${val:,.2f}"
        return f"-${abs(val):,.2f}"

    @staticmethod
    def _fmt_pct(pct: float) -> str:
        """Format percentage with sign."""
        if pct >= 0:
            return f"+{pct:.1f}%"
        return f"{pct:.1f}%"
