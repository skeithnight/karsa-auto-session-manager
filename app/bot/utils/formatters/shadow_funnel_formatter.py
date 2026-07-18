from app.bot.utils.format import bold, italic, code, pre, fmt

def format_shadow_funnel(metrics: dict, performance_report: str) -> str:
    """Format the Shadow Funnel E2E Pipeline Report."""
    _dash = "━" * 32
    
    universe_att = metrics.get("universe_attempted", 0)
    universe_proc = metrics.get("universe_processed", 0)
    
    alpha_gen = metrics.get("alpha_generated", 0)
    alpha_pass = metrics.get("alpha_passed", 0)
    
    ai_calls = metrics.get("ai_calls", 0)
    ai_appr = metrics.get("ai_approvals", 0)
    
    risk_pass = metrics.get("risk_passed", 0)
    risk_rej = metrics.get("risk_rejected", 0)
    
    trade_ord = metrics.get("trade_orders", 0)
    trade_sl = metrics.get("trade_sl_hits", 0)
    trade_ex = metrics.get("trade_exits", 0)

    funnel_ui = (
        f"📡 UNIVERSE\n"
        f"  ↳ Attempted: {universe_att:,}\n"
        f"  ↳ Processed: {universe_proc:,}\n\n"
        
        f"🧠 ALPHA BRIDGE\n"
        f"  ↳ Signals Gen: {alpha_gen:,}\n"
        f"  ↳ Conf. Passed: {alpha_pass:,}\n\n"
        
        f"🤖 AI ANALYST\n"
        f"  ↳ Calls: {ai_calls:,}\n"
        f"  ↳ Approvals: {ai_appr:,}\n\n"
        
        f"🛡️ RISK GATE\n"
        f"  ↳ Passed: {risk_pass:,}\n"
        f"  ↳ Rejected: {risk_rej:,}\n\n"
        
        f"⚡ TRADE & SOR\n"
        f"  ↳ Virtual Exec: {trade_ord:,}\n"
        f"  ↳ SL Hits: {trade_sl:,}\n"
        f"  ↳ TP/Trailing: {trade_ex:,}\n"
    )

    return fmt(
        bold("👥 SHADOW FUNNEL"), "\n",
        _dash, "\n\n",
        pre(funnel_ui), "\n",
        _dash, "\n\n",
        pre(performance_report), "\n\n",
        italic("Virtual trades executed in Shadow Mode.")
    )
