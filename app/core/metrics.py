"""Prometheus metrics for Data Engine, Alpha Bridge, Risk Gate, Executor, ASM."""

from prometheus_client import Counter, Gauge, Histogram

# ── Data Engine ──────────────────────────────────────────────
orderbook_received = Counter(
    "karsa_orderbook_received_total",
    "Orderbooks received from exchanges",
    ["exchange", "symbol"],
)

orderbook_normalized = Counter(
    "karsa_orderbook_normalized_total",
    "Orderbooks successfully normalized",
    ["exchange", "symbol"],
)

orderbook_errors = Counter(
    "karsa_orderbook_errors_total",
    "Orderbook normalization errors",
    ["exchange", "symbol", "error_type"],
)

bad_tick_rejected = Counter(
    "karsa_bad_tick_rejected_total",
    "Bad ticks rejected by filter",
    ["exchange", "symbol"],
)

global_state_written = Counter(
    "karsa_global_state_written_total",
    "Global states written to Redis",
    ["symbol"],
)

vwap_value = Gauge(
    "karsa_vwap_value",
    "Current volume-weighted average price",
    ["symbol"],
)

skew_value = Gauge(
    "karsa_skew_value",
    "Current aggregate bid/ask skew",
    ["symbol"],
)

# ── Pipeline Funnel (flow-stage counters) ──────────────────
regime_classified_total = Counter(
    "karsa_regime_classified_total",
    "Regime classifications performed",
    ["regime"],
)

strategy_scored_total = Counter(
    "karsa_strategy_scored_total",
    "Signals scored by StrategyRouter",
    ["regime", "score_bucket"],
)

signal_confidence_passed_total = Counter(
    "karsa_signal_confidence_passed_total",
    "Signals that passed confidence gate",
    ["regime"],
)

signals_killed_total = Counter(
    "karsa_signals_killed_total",
    "Signals killed at each pipeline stage",
    ["stage", "reason"],
)

# ── Alpha Bridge ─────────────────────────────────────────────
signals_generated = Counter(
    "karsa_signals_generated_total",
    "Signals generated",
    ["symbol", "direction"],
)

signal_confidence = Histogram(
    "karsa_signal_confidence",
    "Signal confidence distribution",
    ["symbol"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

strategy_score = Histogram(
    "karsa_strategy_score",
    "StrategyRouter score per signal (0-100, gate at 65)",
    ["symbol", "regime"],
    buckets=[10, 20, 30, 40, 50, 60, 65, 70, 80, 90, 100],
)

signals_skipped = Counter(
    "karsa_signals_skipped_total",
    "Signals skipped (confidence too low)",
    ["symbol", "reason"],
)

# ── Risk Gate ────────────────────────────────────────────────
risk_gate_pass = Counter(
    "karsa_risk_gate_pass_total",
    "Signals passing risk gate",
    ["symbol"],
)

risk_gate_reject = Counter(
    "karsa_risk_gate_reject_total",
    "Signals rejected by risk gate",
    ["symbol", "reason"],
)

# ── Executor ─────────────────────────────────────────────────
orders_placed = Counter(
    "karsa_orders_placed_total",
    "Orders placed on Bybit",
    ["symbol", "side"],
)

orders_failed = Counter(
    "karsa_orders_failed_total",
    "Order placement failures",
    ["symbol", "error_type"],
)

# ── ASM ──────────────────────────────────────────────────────
asm_session_active = Gauge(
    "karsa_asm_session_active",
    "Autonomous session active (1=active, 0=idle)",
)

asm_risk_pct = Gauge(
    "karsa_asm_risk_pct",
    "Current ASM risk percentage",
)

# ── Regime Engine ────────────────────────────────────────────
regime_state = Gauge(
    "karsa_regime_state",
    "Current regime (0=CHOP,1=MR,2=BEAR,3=BULL)",
)
regime_hurst = Gauge(
    "karsa_regime_hurst",
    "Hurst exponent value",
)
regime_adx = Gauge(
    "karsa_regime_adx",
    "ADX value (1H)",
)
regime_adx_4h = Gauge(
    "karsa_regime_adx_4h",
    "ADX value (4H) — debug AND-gate visibility",
)

# ── Watchdog ────────────────────────────────────────────────
heartbeat_age = Gauge(
    "karsa_heartbeat_age_seconds",
    "Age of last heartbeat per exchange",
    ["exchange"],
)

event_loop_lag = Gauge(
    "karsa_event_loop_lag_ms",
    "Event loop lag in milliseconds",
)

execution_latency = Histogram(
    "karsa_execution_latency_seconds",
    "Signal-to-fill latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
)

dms_ping_success = Counter(
    "karsa_dms_ping_success_total",
    "Dead Man's Switch successful pings",
)

dms_ping_failure = Counter(
    "karsa_dms_ping_failure_total",
    "Dead Man's Switch failed pings",
)

alpha_bridge_paused = Gauge(
    "karsa_alpha_bridge_paused",
    "Alpha Bridge paused (1=paused, 0=active)",
)

positions_flattened_total = Counter(
    "karsa_positions_flattened_total",
    "Positions flattened by watchdog",
    ["reason"],
)

# ── Symbol Validation ────────────────────────────────────────
symbol_universe_total = Gauge(
    "karsa_symbol_universe_total",
    "Valid symbols after cross-exchange validation",
)

symbol_universe_dropped = Gauge(
    "karsa_symbol_universe_dropped",
    "Symbols dropped during cross-exchange validation",
)

universe_symbols_scored = Counter(
    "karsa_universe_symbols_scored_total",
    "Symbols scored by UniverseScorer each cycle",
)

# ── Network Health ───────────────────────────────────────────
vpn_status = Gauge(
    "karsa_vpn_status",
    "VPN tunnel reachable (1=up, 0=down)",
)

bybit_status = Gauge(
    "karsa_bybit_status",
    "Bybit connected (1=up, 0=down)",
)

exchange_status = Gauge(
    "karsa_exchange_status",
    "Exchange feed status (0=ACTIVE, 1=STALE, 2=DEGRADED)",
    ["exchange"],
)

ws_heartbeat_age = Gauge(
    "karsa_ws_heartbeat_age_seconds",
    "Seconds since last WS message per exchange",
    ["exchange"],
)

ws_disconnects = Counter(
    "karsa_websocket_disconnects_total",
    "WebSocket disconnect events",
    ["exchange"],
)

# ── AI Integration ───────────────────────────────────────────
ai_analyst_calls = Counter(
    "karsa_ai_analyst_calls_total",
    "AI analyst call outcomes",
    ["result"],
)

ai_analyst_latency = Histogram(
    "karsa_ai_analyst_latency_seconds",
    "AI analyst call latency",
    buckets=[0.5, 1, 2, 5, 10, 15, 30],
)

ai_analyst_rejections = Counter(
    "karsa_ai_analyst_rejections_total",
    "AI analyst rejections by reason",
    ["reason"],
)

ai_judge_verdict = Counter(
    "karsa_ai_judge_verdict_total",
    "Position judge verdicts",
    ["symbol", "verdict", "tier"],
)

ai_judge_latency = Histogram(
    "karsa_position_judge_latency_seconds",
    "Position judge call latency",
    buckets=[0.5, 1, 2, 5, 10, 15, 30],
)

ai_consecutive_hold_exits = Counter(
    "karsa_position_judge_consecutive_hold_exits_total",
    "Forced exits after 3 consecutive HOLDs on losing position",
)

trade_memory_stored = Counter(
    "karsa_trade_memory_entries_stored_total",
    "Trade memory entries stored on close",
    ["symbol"],
)

trade_memory_injected = Counter(
    "karsa_trade_memory_injection_hits_total",
    "Trade memory context injected into AI prompt",
    ["symbol"],
)

# ── Position Lifecycle ───────────────────────────────────────
position_unrealized_pnl = Gauge(
    "karsa_position_unrealized_pnl_usdt",
    "Unrealized PnL in USDT",
    ["symbol"],
)

wallet_balance = Gauge(
    "karsa_wallet_balance_usdt",
    "Available wallet balance in USDT",
)

position_size = Gauge(
    "karsa_position_size",
    "Position size (contracts)",
    ["symbol"],
)

position_entry_price = Gauge(
    "karsa_position_entry_price_usdt",
    "Position entry price in USDT",
    ["symbol"],
)

position_duration = Gauge(
    "karsa_position_duration_seconds",
    "Time since position opened",
    ["symbol"],
)

position_sl_price = Gauge(
    "karsa_position_sl_price_usdt",
    "Current stop-loss price for active position",
    ["symbol"],
)

stop_loss_placement = Counter(
    "karsa_stop_loss_placement_total",
    "Stop-loss placement attempts",
    ["symbol", "result"],
)

sl_tp_atomic_placement = Counter(
    "karsa_sl_tp_atomic_placement_total",
    "Atomic SL/TP placement via set_trading_stop",
    ["symbol"],
)

position_lifecycle_duration = Histogram(
    "karsa_position_lifecycle_duration_seconds",
    "Time from position open to close",
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800],
)

pnl_unrealized_drift = Gauge(
    "karsa_pnl_unrealized_drift_pct",
    "Unrealized PnL drift between exchange and local calculation",
    ["symbol"],
)

# ── Data Integrity ───────────────────────────────────────────
postgres_write_errors = Counter(
    "karsa_postgres_write_errors_total",
    "Failed Postgres writes",
    ["table"],
)

signals_pipeline_attempted = Counter(
    "karsa_signals_pipeline_attempted_total",
    "Symbols attempted through signal pipeline (before generate)",
    ["symbol"],
)

signals_entered_pipeline = Counter(
    "karsa_signals_entered_pipeline_total",
    "Signals entering the full 6-stage pipeline (after generate)",
    ["symbol"],
)

signals_completed_pipeline = Counter(
    "karsa_signals_completed_pipeline_total",
    "Signals completed or rejected at pipeline end",
    ["symbol", "outcome"],
)

# ── Proxy Latency ────────────────────────────────────────────
proxy_latency = Histogram(
    "karsa_proxy_latency_ms",
    "WARP proxy round-trip latency in ms",
    buckets=[50, 100, 200, 500, 1000, 2000, 5000],
)

# ── Position Lifecycle ───────────────────────────────────────
positions_opened = Counter(
    "karsa_positions_opened_total",
    "Positions opened",
    ["symbol", "side"],
)

positions_closed = Counter(
    "karsa_positions_closed_total",
    "Positions closed",
    ["symbol", "side", "exit_reason"],
)

position_pnl = Histogram(
    "karsa_position_pnl_usd",
    "Position PnL in USD",
    ["symbol"],
    buckets=[-50, -20, -10, -5, -1, 0, 1, 5, 10, 20, 50, 100],
)

# ── Critical Task Liveness ───────────────────────────────────
critical_task_dead = Gauge(
    "karsa_critical_task_dead",
    "Critical task liveness (1=dead, 0=alive)",
    ["task"],
)

# ── Circuit Breaker ──────────────────────────────────────────
circuit_breaker_trips = Counter(
    "karsa_circuit_breaker_trips_total",
    "Circuit breaker triggered",
    ["breaker_name"],
)

# ── AI Calls ─────────────────────────────────────────────────
ai_calls_total = Counter(
    "karsa_ai_calls_total",
    "AI service calls",
    ["service", "outcome"],
)

ai_call_latency = Histogram(
    "karsa_ai_call_latency_seconds",
    "AI service call latency",
    ["service"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# ── Position Reconciler ──────────────────────────────────────
reconciler_stale_removed = Counter(
    "karsa_reconciler_stale_removed_total",
    "Stale positions removed by reconciler",
    ["symbol"],
)

# ── Trade Reconciler ─────────────────────────────────────────
trade_reconcile_cycles = Counter(
    "karsa_trade_reconcile_cycles_total",
    "Trade history reconciliation cycles completed",
)
trade_reconcile_fills_checked = Counter(
    "karsa_trade_reconcile_fills_checked_total",
    "Bybit fill records checked by reconciler",
)
trade_reconcile_discrepancies = Counter(
    "karsa_trade_reconcile_discrepancies_total",
    "Trade discrepancies found by reconciler",
    ["kind"],
)
trade_reconcile_repairs = Counter(
    "karsa_trade_reconcile_repairs_total",
    "Trades auto-repaired by reconciler",
    ["kind"],
)
trade_reconcile_errors = Counter(
    "karsa_trade_reconcile_errors_total",
    "Trade reconciliation cycle errors",
    ["error_type"],
)

wallet_total_equity = Gauge(
    "karsa_wallet_total_equity_usdt",
    "Total equity in USDT",
)

# ── Funnel Dashboard ─────────────────────────────────────────
universe_size = Gauge(
    "karsa_universe_size",
    "Current active universe symbol count",
)

data_age_seconds = Gauge(
    "karsa_data_age_seconds",
    "Seconds since last successful data fetch",
    ["symbol"],
)

data_fetch_total = Counter(
    "karsa_data_fetch_total",
    "Data fetch attempts by field and result",
    ["symbol", "field", "result"],
)

execution_slippage_bps = Histogram(
    "karsa_execution_slippage_bps",
    "Entry vs fill price delta in basis points",
    ["symbol"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 50],
)

sor_step_total = Counter(
    "karsa_sor_step_total",
    "SOR execution step that filled the order",
    ["symbol", "step"],
)

param_threshold = Gauge(
    "karsa_param_threshold",
    "Current tunable threshold values",
    ["name"],
)

ai_confidence = Gauge(
    "karsa_ai_confidence",
    "Latest AI Analyst confidence score",
    ["symbol"],
)

max_positions = Gauge(
    "karsa_asm_max_positions",
    "Maximum allowed open positions",
)

# ── Shadow Mode ─────────────────────────────────────────────
karsa_shadow_mode_active = Gauge(
    "karsa_shadow_mode_active",
    "Shadow mode enabled (1=active, 0=inactive)",
)

karsa_shadow_orders_placed_total = Counter(
    "karsa_shadow_orders_placed_total",
    "Shadow virtual orders placed",
    ["symbol", "side"],
)

karsa_shadow_exits_placed_total = Counter(
    "karsa_shadow_exits_placed_total",
    "Shadow virtual exits placed",
    ["symbol", "reason"],
)

karsa_shadow_pnl_usdt = Histogram(
    "karsa_shadow_pnl_usdt",
    "Shadow virtual PnL in USDT",
    buckets=[-100, -50, -20, -10, -5, -1, 0, 1, 5, 10, 20, 50, 100],
)

karsa_shadow_fees_total_usdt = Counter(
    "karsa_shadow_fees_total_usdt",
    "Total shadow trading fees in USDT",
)

karsa_shadow_slippage_total_usdt = Counter(
    "karsa_shadow_slippage_total_usdt",
    "Total shadow slippage cost in USDT",
)

karsa_shadow_positions_open = Gauge(
    "karsa_shadow_positions_open",
    "Currently open shadow positions",
)

karsa_shadow_sl_hits_total = Counter(
    "karsa_shadow_sl_hits_total",
    "Shadow SL hits triggered",
    ["symbol", "side"],
)

karsa_shadow_tp_hits_total = Counter(
    "karsa_shadow_tp_hits_total",
    "Shadow TP hits triggered",
    ["symbol", "side"],
)

karsa_shadow_time_exits_total = Counter(
    "karsa_shadow_time_exits_total",
    "Shadow time-based exits",
    ["symbol", "side"],
)

karsa_shadow_funding_fees_total_usdt = Counter(
    "karsa_shadow_funding_fees_total_usdt",
    "Total shadow funding fees in USDT",
)

karsa_shadow_limit_orders_unfilled_total = Counter(
    "karsa_shadow_limit_orders_unfilled_total",
    "Shadow post-only limit orders expired unfilled",
    ["symbol"],
)

# ── Live Mode ────────────────────────────────────────────────
karsa_live_orders_placed_total = Counter(
    "karsa_live_orders_placed_total",
    "Live orders placed on Bybit",
    ["symbol", "side"],
)

karsa_live_exits_placed_total = Counter(
    "karsa_live_exits_placed_total",
    "Live exits placed",
    ["symbol", "reason"],
)

karsa_live_sl_hits_total = Counter(
    "karsa_live_sl_hits_total",
    "Live SL hits triggered",
    ["symbol", "side"],
)

karsa_shadow_stale_cleanups_total = Counter(
    "karsa_shadow_stale_cleanups_total",
    "Shadow positions auto-closed for missing SL",
    ["symbol", "side"],
)


def get_metric_sum(metric_name: str, is_counter: bool = True) -> float:
    """Helper to sum prometheus metric values across all labels via Prometheus API."""
    import json
    import urllib.parse
    import urllib.request

    from loguru import logger

    total = 0.0
    try:
        query_name = metric_name
        if is_counter and not query_name.endswith("_total"):
            query_name += "_total"

        url = f"http://prometheus:9090/api/v1/query?query={urllib.parse.quote(query_name)}"
        req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=2.0) as response:
            data = json.loads(response.read())

        if data.get("status") == "success":
            for res in data.get("data", {}).get("result", []):
                val_str = res.get("value", [0, "0"])[1]
                total += float(val_str)
    except Exception as e:
        logger.error(f"Failed to fetch {metric_name} from Prometheus: {e}")

    return total


def get_funnel_metrics() -> dict:
    """Fetch all funnel metrics for the shadow pipeline."""
    return {
        "universe_attempted": int(get_metric_sum("karsa_signals_pipeline_attempted")),
        "universe_processed": int(get_metric_sum("karsa_signals_entered_pipeline")),
        "alpha_generated": int(get_metric_sum("karsa_signals_generated")),
        "alpha_passed": int(get_metric_sum("karsa_signal_confidence_passed")),
        "ai_calls": int(get_metric_sum("karsa_ai_analyst_calls")),
        "ai_approvals": max(0, int(get_metric_sum("karsa_ai_analyst_calls"))
        - int(get_metric_sum("karsa_ai_analyst_rejections"))),
        "risk_passed": int(get_metric_sum("karsa_risk_gate_pass")),
        "risk_rejected": int(get_metric_sum("karsa_risk_gate_reject")),
        "trade_orders": int(get_metric_sum("karsa_shadow_orders_placed")),
        "trade_sl_hits": int(get_metric_sum("karsa_shadow_sl_hits")),
        "trade_exits": int(get_metric_sum("karsa_shadow_exits_placed")),
    }


def get_live_funnel_metrics() -> dict:
    """Fetch all funnel metrics for the live pipeline."""
    return {
        "universe_attempted": int(get_metric_sum("karsa_signals_pipeline_attempted")),
        "universe_processed": int(get_metric_sum("karsa_signals_entered_pipeline")),
        "alpha_generated": int(get_metric_sum("karsa_signals_generated")),
        "alpha_passed": int(get_metric_sum("karsa_signal_confidence_passed")),
        "ai_calls": int(get_metric_sum("karsa_ai_analyst_calls")),
        "ai_approvals": max(0, int(get_metric_sum("karsa_ai_analyst_calls"))
        - int(get_metric_sum("karsa_ai_analyst_rejections"))),
        "risk_passed": int(get_metric_sum("karsa_risk_gate_pass")),
        "risk_rejected": int(get_metric_sum("karsa_risk_gate_reject")),
        "trade_orders": int(get_metric_sum("karsa_orders_placed")),
        "trade_sl_hits": int(get_metric_sum("karsa_stop_loss_placement")),
        "trade_exits": int(get_metric_sum("karsa_positions_closed")),
    }
