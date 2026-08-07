"""Shadow Mode 4-Hour Automated Performance Monitor.

Fetches shadow active positions, 4-hour performance metrics, closed trades from Postgres,
rejected signal stats, and dynamic universe metrics every 4 hours and sends rich Telegram reports.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import DatabaseEngine
from app.core.redis_client import RedisClient


async def run_shadow_audit(redis_client: RedisClient, db_engine: DatabaseEngine) -> dict:
    """Collect real-time performance snapshot of the shadow trading engine."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe": {"total": 41, "categories": {}},
        "active_positions": [],
        "performance_4h": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "total_fees": 0.0,
            "exit_reasons": {},
        },
        "recent_trades": [],
        "rejected_signals": 0,
    }

    try:
        # 1. Universe Metrics
        raw_univ = await redis_client.get("system:universe:symbols")
        if raw_univ:
            univ_data = json.loads(raw_univ)
            symbols = univ_data.get("symbols", [])
            categories = univ_data.get("categories", {})
            snapshot["universe"]["total"] = len(symbols) or 41
            cat_counts = {}
            for s in symbols:
                cat = categories.get(s, "VOLUME_LEADER")
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
            snapshot["universe"]["categories"] = cat_counts

        # 2. Active Shadow Positions (Fixed prefix: shadow:position:*)
        pos_keys = await redis_client.keys("shadow:position:*")
        for pk in pos_keys:
            raw_pos = await redis_client.get(pk)
            if raw_pos:
                try:
                    pos = json.loads(raw_pos)
                    snapshot["active_positions"].append({
                        "symbol": pos.get("symbol", ""),
                        "side": pos.get("side", ""),
                        "entry_price": pos.get("entry_price", "0"),
                        "virtual_sl": pos.get("virtual_sl", "N/A"),
                        "virtual_tp": pos.get("virtual_tp", "N/A"),
                        "regime": pos.get("regime", ""),
                    })
                except Exception:
                    pass

        # 3. 4-Hour Closed Performance from PostgreSQL
        if db_engine.engine is not None:
            async with db_engine.engine.connect() as conn:
                res = await conn.execute(text(
                    "SELECT symbol, side, entry_price, exit_price, pnl, exit_reason, fees_applied, created_at "
                    "FROM shadow_trades WHERE created_at >= NOW() - INTERVAL '4 hours' ORDER BY created_at DESC"
                ))
                trades = [dict(r._mapping) for r in res]
                snapshot["recent_trades"] = trades[:5]
                
                if trades:
                    winners = [t for t in trades if t["pnl"] and t["pnl"] > 0]
                    losers = [t for t in trades if t["pnl"] and t["pnl"] < 0]
                    gp = sum((t["pnl"] for t in winners), Decimal("0"))
                    gl = abs(sum((t["pnl"] for t in losers), Decimal("0")))
                    net_pnl = gp - gl
                    tot_fees = sum((t["fees_applied"] for t in trades if t["fees_applied"]), Decimal("0"))
                    
                    pf = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
                    wr = (len(winners) / len(trades) * 100) if trades else 0.0

                    reasons = {}
                    for t in trades:
                        r_name = t.get("exit_reason") or "unknown"
                        reasons[r_name] = reasons.get(r_name, 0) + 1

                    snapshot["performance_4h"] = {
                        "total_trades": len(trades),
                        "wins": len(winners),
                        "losses": len(losers),
                        "win_rate": round(wr, 1),
                        "net_pnl": round(float(net_pnl), 4),
                        "gross_profit": round(float(gp), 4),
                        "gross_loss": round(float(gl), 4),
                        "profit_factor": round(pf, 2),
                        "total_fees": round(float(tot_fees), 4),
                        "exit_reasons": reasons,
                    }

        # 4. Rejected Signals Count
        raw_rej = await redis_client.redis.xlen("karsa:rejected_signals") if hasattr(redis_client, "redis") else 0
        snapshot["rejected_signals"] = raw_rej or 0

        logger.info(
            "Shadow Monitor Snapshot: ActivePos=%d 4H_Trades=%d 4H_PnL=%.4f USD",
            len(snapshot["active_positions"]),
            snapshot["performance_4h"]["total_trades"],
            snapshot["performance_4h"]["net_pnl"],
        )
    except Exception as e:
        logger.error(f"Shadow Monitor Audit failed: {e}")

    return snapshot


def build_4h_shadow_telegram_message(snapshot: dict) -> str:
    """Format rich, trader-ready 4-Hour Shadow Performance Report."""
    now_str = snapshot["timestamp"]
    perf = snapshot["performance_4h"]
    active_pos = snapshot["active_positions"]
    total_univ = snapshot["universe"]["total"]
    rejected = snapshot["rejected_signals"]

    # Active positions block
    if active_pos:
        pos_lines = []
        for p in active_pos:
            sl_p = f"${float(p['virtual_sl']):,.2f}" if p['virtual_sl'] != "N/A" else "N/A"
            tp_p = f"${float(p['virtual_tp']):,.2f}" if p['virtual_tp'] != "N/A" else "N/A"
            entry_p = f"${float(p['entry_price']):,.2f}" if p['entry_price'] != "0" else "?"
            pos_lines.append(f"  • {p['symbol']} {p['side']} @ {entry_p}\n    SL: {sl_p} | TP: {tp_p}")
        pos_block = "\n".join(pos_lines)
    else:
        pos_block = "  • None (All positions closed)"

    # Exit reasons block
    if perf["exit_reasons"]:
        exit_lines = [f"  • {reason:<22} : {count} trades" for reason, count in sorted(perf["exit_reasons"].items(), key=lambda x: -x[1])]
        exit_block = "\n".join(exit_lines)
    else:
        exit_block = "  • No trades closed in last 4H"

    net_pnl_val = perf["net_pnl"]
    pnl_sign = "+$" if net_pnl_val >= 0 else "-$"

    card_text = (
        f"{now_str} | SHADOW ENGINE REPORT\n"
        f"Status: 🟢 SHADOW RUNNING | Active Universe: {total_univ} Pairs\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 4-HOUR PERFORMANCE METRICS\n"
        f"  Closed Trades : {perf['total_trades']} trades ({perf['wins']}W / {perf['losses']}L)\n"
        f"  Win Rate      : {perf['win_rate']}%\n"
        f"  Net PnL       : {pnl_sign}{abs(net_pnl_val):,.4f} USD\n"
        f"  Gross Profit  : +${perf['gross_profit']:,.4f} USD\n"
        f"  Gross Loss    : -${perf['gross_loss']:,.4f} USD\n"
        f"  Profit Factor : {perf['profit_factor']}\n\n"
        f"📍 ACTIVE SHADOW POSITIONS ({len(active_pos)} Open)\n"
        f"{pos_block}\n\n"
        f"📡 SIGNAL PIPELINE & EXECUTIONS (Last 4H)\n"
        f"  Rejected      : {rejected} signals tracked\n"
        f"  Execution     : Post-Only Maker (0.02% Fee)\n\n"
        f"🚪 TOP EXIT REASONS\n"
        f"{exit_block}"
    )

    return (
        f"📊 <b>4-HOUR SHADOW PERFORMANCE SUMMARY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<pre>{card_text}</pre>"
    )


async def main_loop():
    logger.info("=== SHADOW MODE 4-HOUR AUTOMATED MONITORING LAUNCHED ===")
    settings = get_settings()

    redis = RedisClient()
    await redis.connect()

    db = DatabaseEngine()
    await db.connect(settings.postgres_url)

    interval_s = 4 * 3600  # 4 Hours
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    report_file = os.path.join(log_dir, "shadow_monitor_reports.jsonl")

    from app.bot.alert_service import AlertService
    alert_service = AlertService(settings.telegram_chat_id) if settings.telegram_chat_id else None

    if alert_service and settings.telegram_bot_token:
        try:
            import telegram
            bot = telegram.Bot(token=settings.telegram_bot_token)
            alert_service.register_bot(bot)
        except Exception as be:
            logger.warning(f"Shadow monitor Telegram bot init warning: {be}")

    while True:
        snapshot = await run_shadow_audit(redis, db)
        with open(report_file, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
        logger.info(f"Report written to {report_file}.")

        if alert_service:
            try:
                msg = build_4h_shadow_telegram_message(snapshot)
                await alert_service.send(msg)
                logger.info("Sent 4-hour shadow performance summary to Telegram")
            except Exception as se:
                logger.warning(f"Failed to send 4-hour shadow Telegram summary: {se}")

        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    asyncio.run(main_loop())
