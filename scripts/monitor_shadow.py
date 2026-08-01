"""Shadow Mode 15-Minute Automated Diagnostic Monitor.

Fetches shadow active positions, completed shadow trades, rejected signal stats,
and dynamic universe metrics every 15 minutes and writes periodic reports.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger

from app.core.redis_client import RedisClient


async def run_shadow_audit(redis_client: RedisClient) -> dict:
    """Collect real-time performance snapshot of the shadow trading engine."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "universe": {"total": 0, "categories": {}},
        "active_positions": [],
        "shadow_trades": {"total_count": 0, "win_rate_pct": 0.0, "net_pnl_usd": 0.0, "profit_factor": 0.0},
        "rejected_signals": {"total_count": 0, "avg_hypothetical_ev": 0.0},
    }

    try:
        # 1. Universe Metrics
        raw_univ = await redis_client.get("system:universe:symbols")
        if raw_univ:
            univ_data = json.loads(raw_univ)
            symbols = univ_data.get("symbols", [])
            categories = univ_data.get("categories", {})
            snapshot["universe"]["total"] = len(symbols)
            cat_counts = {}
            for s in symbols:
                cat = categories.get(s, "VOLUME_LEADER")
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
            snapshot["universe"]["categories"] = cat_counts

        # 2. Active Shadow Positions
        pos_keys = await redis_client.keys("karsa:position:*")
        for pk in pos_keys:
            raw_pos = await redis_client.get(pk)
            if raw_pos:
                try:
                    pos = json.loads(raw_pos)
                    snapshot["active_positions"].append({
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side"),
                        "entry_price": pos.get("entry_price"),
                        "regime": pos.get("regime"),
                        "worst_price_seen": pos.get("worst_price_seen"),
                    })
                except Exception:
                    pass

        # 3. Rejected Signals Stream
        raw_rej = await redis_client.redis.xlen("karsa:rejected_signals") if hasattr(redis_client, "redis") else 0
        snapshot["rejected_signals"]["total_count"] = raw_rej or 0

        logger.info(
            "Shadow Monitor Snapshot: Universe=%d (Accumulation=%d, Momentum=%d) ActivePos=%d RejectedSignals=%d",
            snapshot["universe"]["total"],
            snapshot["universe"]["categories"].get("ACCUMULATION", 0),
            snapshot["universe"]["categories"].get("MOMENTUM_SQUEEZE", 0) + snapshot["universe"]["categories"].get("MOMENTUM_BREAKOUT", 0),
            len(snapshot["active_positions"]),
            snapshot["rejected_signals"]["total_count"],
        )
    except Exception as e:
        logger.error(f"Shadow Monitor Audit failed: {e}")

    return snapshot


async def main_loop():
    logger.info("=== SHADOW MODE 15-MINUTE AUTOMATED MONITORING LAUNCHED ===")
    redis = RedisClient()
    await redis.connect()

    interval_s = 15 * 60  # 15 minutes
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    report_file = os.path.join(log_dir, "shadow_monitor_reports.jsonl")
    iteration = 0

    from app.core.config import get_settings
    from app.bot.alert_service import AlertService
    settings = get_settings()
    alert_service = AlertService(settings.telegram_chat_id) if settings.telegram_chat_id else None

    if alert_service and settings.telegram_bot_token:
        try:
            import telegram
            bot = telegram.Bot(token=settings.telegram_bot_token)
            alert_service.register_bot(bot)
        except Exception as be:
            logger.warning(f"Shadow monitor Telegram bot init warning: {be}")

    while True:
        snapshot = await run_shadow_audit(redis)
        with open(report_file, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
        logger.info(f"Report written to {report_file}.")

        iteration += 1
        # Send Telegram Summary every 30 minutes (iteration % 2 == 0)
        if iteration % 2 == 0 and alert_service:
            try:
                active_pos_str = ", ".join([p["symbol"] for p in snapshot["active_positions"]]) if snapshot["active_positions"] else "None"
                msg = (
                    "📊 <b>30-MINUTE SHADOW PERFORMANCE SUMMARY</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>Universe Active:</b> {snapshot['universe']['total']} Symbols\n"
                    f"<b>Active Positions:</b> {len(snapshot['active_positions'])} ({active_pos_str})\n"
                    f"<b>Rejected Signals:</b> {snapshot['rejected_signals']['total_count']}\n"
                    "<b>Execution Mode:</b> 🟢 Post-Only Maker (0.02% Fee)\n"
                    "<i>Shadow mode is testing silently. Zero individual trade spam.</i>"
                )
                await alert_service.send(msg)
                logger.info("Sent 30-minute shadow summary to Telegram")
            except Exception as se:
                logger.warning(f"Failed to send shadow Telegram summary: {se}")

        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    asyncio.run(main_loop())
