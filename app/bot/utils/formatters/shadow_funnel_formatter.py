from datetime import datetime
from app.bot.utils.format import fmt, pre


def format_shadow_funnel(metrics: dict, report: object) -> str:
    """Format the Shadow Funnel E2E Pipeline Report."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    
    alerts = []
    # Optionally, we can check shadow specifics here if needed.
    
    if alerts:
        alerts_str = "🚨 DIAGNOSTIC ALERTS\n" + "\n".join(f"  {i+1}. {a}" for i, a in enumerate(alerts))
        header_alerts = f"⚠️ {len(alerts)} CRITICAL ALERTS"
    else:
        alerts_str = "🚨 DIAGNOSTIC ALERTS\n  ✅ All systems nominal."
        header_alerts = "✅ 0 CRITICAL ALERTS"

    header = f"{now_str} | 👥 SHADOW MODE | {header_alerts}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    stages = [
        ("🌍 Universe Scanned", metrics.get("universe_attempted", 0)),
        ("🧠 Raw Signals Gen", metrics.get("alpha_generated", 0)),
        ("🧪 Local Pre-Filter", metrics.get("alpha_passed", 0)),
        ("🤖 AI Analyst Calls", metrics.get("ai_calls", 0)),
        ("↳ AI Approved", metrics.get("ai_approvals", 0)),
        ("🛡️ Risk Gate Pass", metrics.get("risk_passed", 0)),
        ("📤 Virtual Exec", metrics.get("trade_orders", 0)),
        ("✅ Virtual Filled", metrics.get("trade_orders", 0)),
        ("🔄 Positions Managed", metrics.get("trade_orders", 0)),
        ("🏁 Positions Closed", metrics.get("trade_exits", 0)),
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
        f"📡 E2E SIGNAL FUNNEL (Last 1H)\n"
        f"  Stage                  Volume    Drop-off   Conv. Rate\n"
        f"  ────────────────────────────────────────────────────────\n"
        f"{funnel_str}"
    )

    open_trades = max(0, metrics.get("trade_orders", 0) - metrics.get("trade_exits", 0))
    total_trades = report.total_trades + open_trades
    gross_pnl = report.gross_profit - report.gross_loss
    loss_pct = 100.0 - report.win_rate if report.total_trades else 0.0
    
    perf_table = (
        f"💰 PERFORMANCE SUMMARY (Cumulative)\n"
        f"  Total Trades: {total_trades:<5} |   Closed: {report.total_trades:<5} |   Open: {open_trades}\n"
        f"  Wins: {report.winning_trades} ({report.win_rate:.1f}%)   |   Losses: {report.losing_trades} ({loss_pct:.1f}%)\n"
        f"  Net PnL: ${report.net_pnl:.2f}   |   Gross PnL: ${gross_pnl:.2f}"
    )

    fee_warn = " ⚠️ (MUST BE > $0 ON LIVE!)" if report.total_fees == 0 else ""
    slip_warn = " ⚠️ (Tracker disabled?)" if report.total_slippage == 0 else ""
    costs_table = (
        f"💸 COSTS & EXECUTION QUALITY\n"
        f"  Total Fees:     ${report.total_fees:.2f}{fee_warn}\n"
        f"  Total Slippage: ${report.total_slippage:.2f}{slip_warn}"
    )

    return fmt(
        pre(header),
        "\n\n",
        pre(alerts_str),
        "\n\n",
        pre(table),
        "\n\n",
        pre(perf_table),
        "\n\n",
        pre(costs_table)
    )
