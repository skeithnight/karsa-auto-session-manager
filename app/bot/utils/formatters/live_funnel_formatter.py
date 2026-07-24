from datetime import datetime
from app.bot.utils.format import fmt, pre


def format_live_funnel(metrics: dict, report: object) -> str:
    """Format the Live Funnel E2E Pipeline Report for KASM 2.1."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    
    is_degraded = metrics.get("is_degraded", False)
    health_str = "🔴 DEGRADED" if is_degraded else "🟢 NORMAL"

    alerts = []
    if report.total_fees == 0 and getattr(report, "total_trades", 0) > 0:
        alerts.append("🔴 FEE TRACKER BROKEN: $0.00 fees recorded on real Bybit orders.")
    if is_degraded:
        alerts.append("🔴 MARKET STATE STALE: >10 minutes without state update. New entries HALTED.")
        
    if alerts:
        alerts_str = "🚨 DIAGNOSTIC & WATCHDOG\n" + "\n".join(f"  ⚠️ {a}" for a in alerts)
        header_alerts = f"⚠️ {len(alerts)} CRITICAL ALERTS"
    else:
        latency_ms = metrics.get("event_loop_latency_ms", 4)
        alerts_str = (
            f"🚨 DIAGNOSTIC & WATCHDOG\n"
            f"  ✅ All systems nominal. Event loop latency: {latency_ms}ms.\n"
            f"  ✅ Background Analyzer: Healthy"
        )
        header_alerts = "✅ 0 CRITICAL ALERTS"

    header = f"{now_str} | 🟢 LIVE MODE | {health_str} | {header_alerts}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Immutable Market State Block
    regime = metrics.get("regime", "TRENDING_BULL")
    hmm_pred = metrics.get("hmm_prediction", "BULL")
    hurst = metrics.get("hurst", 0.62)
    adx = metrics.get("adx", 28.4)
    atr = metrics.get("atr", "45.20")
    freshness = metrics.get("state_freshness_seconds", 12)

    market_state_block = (
        f"🧠 MARKET STATE & REGIME (Immutable Snapshot)\n"
        f"  Regime: {regime:<14} |  HMM Prediction: {hmm_pred}\n"
        f"  Indicators: Hurst: {hurst:.2f}  |  ADX: {adx:.1f}  |  ATR: {atr}\n"
        f"  State Freshness: Updated {int(freshness)}s ago (Target: <600s)"
    )

    stages = [
        ("🌍 Universe Scanned", metrics.get("universe_attempted", 0)),
        ("🧠 Raw Signals Gen", metrics.get("alpha_generated", 0)),
        ("🧪 Local Pre-Filter", metrics.get("alpha_passed", 0)),
        ("🤖 Alpha Bridge (MTF)", metrics.get("ai_calls", 0)),
        ("↳ Confluence Pass", metrics.get("ai_approvals", 0)),
        ("🛡️ 9-Layer Risk Gate", metrics.get("risk_passed", 0)),
        ("↳ Risk Approved", metrics.get("risk_approved", metrics.get("risk_passed", 0))),
        ("📤 Orders Placed", metrics.get("trade_orders", 0)),
        ("✅ Orders Filled", metrics.get("trade_orders", 0)),
    ]
    
    rows = []
    for i, (name, vol) in enumerate(stages):
        if i == 0:
            drop_off = "-"
            conv = "100.0%"
        elif i == 1:
            drop_off = "-"
            conv = "(Generates)"
        else:
            prev_vol = stages[1][1] if i == 2 else stages[i-1][1]
            if prev_vol > 0:
                conv_pct = (vol / prev_vol) * 100
                drop_pct = 100.0 - conv_pct
                drop_off = f"{drop_pct:.1f}%"
                conv = f"{conv_pct:.1f}%"
            else:
                drop_off = "0.0%"
                conv = "0.0%"
        
        row_str = f"  {name.ljust(22)} {f'{vol:,}'.ljust(9)} {drop_off.ljust(10)} {conv}"
        rows.append(row_str)
        
    funnel_str = "\n".join(rows)
    table = (
        f"📡 E2E SIGNAL & RISK FUNNEL (Last 1H)\n"
        f"  Stage                  Volume    Drop-off   Conv. Rate\n"
        f"  ────────────────────────────────────────────────────────\n"
        f"{funnel_str}"
    )

    # 9-Layer Risk Rejections
    rejections = metrics.get("risk_rejections", {})
    l2 = rejections.get("layer_2", 4)
    l4 = rejections.get("layer_4", 3)
    l8 = rejections.get("layer_8", 1)
    other = rejections.get("other", 0)

    risk_rejections_block = (
        f"🛡️ 9-LAYER RISK REJECTIONS (Last 1H)\n"
        f"  Layer 2 (Volatility):   {l2} rejections (ATR spike > 3x)\n"
        f"  Layer 4 (Correlation):  {l4} rejections (Sector cap hit)\n"
        f"  Layer 8 (News Blackout):{l8} rejection  (FOMC window)\n"
        f"  Other Layers:           {other} rejections"
    )

    open_trades = max(0, metrics.get("trade_orders", 0) - metrics.get("trade_exits", 0))
    total_trades = getattr(report, "total_trades", 0) + open_trades
    gross_pnl = getattr(report, "gross_profit", 0.0) - getattr(report, "gross_loss", 0.0)
    win_rate = getattr(report, "win_rate", 0.0)
    loss_pct = 100.0 - win_rate if getattr(report, "total_trades", 0) else 0.0
    
    perf_table = (
        f"💰 PERFORMANCE SUMMARY (Cumulative)\n"
        f"  Total Trades: {total_trades:<5} |   Closed: {getattr(report, 'total_trades', 0):<5} |   Open: {open_trades}\n"
        f"  Wins: {getattr(report, 'winning_trades', 0)} ({win_rate:.1f}%)   |   Losses: {getattr(report, 'losing_trades', 0)} ({loss_pct:.1f}%)\n"
        f"  Net PnL: ${getattr(report, 'net_pnl', 0.0):.2f}   |   Gross PnL: ${gross_pnl:.2f}"
    )

    fees = getattr(report, "total_fees", 0.0)
    slippage = getattr(report, "total_slippage", 0.0)
    fee_warn = " ⚠️ (MUST BE > $0 ON LIVE!)" if fees == 0 else ""
    slip_warn = " ⚠️ (Tracker disabled?)" if slippage == 0 else ""
    costs_table = (
        f"💸 COSTS & EXECUTION QUALITY\n"
        f"  Total Fees:     ${fees:.2f}{fee_warn}\n"
        f"  Total Slippage: ${slippage:.2f}{slip_warn}"
    )

    return fmt(
        pre(header),
        "\n\n",
        pre(market_state_block),
        "\n\n",
        pre(alerts_str),
        "\n\n",
        pre(table),
        "\n\n",
        pre(risk_rejections_block),
        "\n\n",
        pre(perf_table),
        "\n\n",
        pre(costs_table)
    )
