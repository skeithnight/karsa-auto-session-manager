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
    "ADX value",
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

stop_loss_placement = Counter(
    "karsa_stop_loss_placement_total",
    "Stop-loss placement attempts",
    ["symbol", "result"],
)

position_lifecycle_duration = Histogram(
    "karsa_position_lifecycle_duration_seconds",
    "Time from position open to close",
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 28800],
)

# ── Data Integrity ───────────────────────────────────────────
postgres_write_errors = Counter(
    "karsa_postgres_write_errors_total",
    "Failed Postgres writes",
    ["table"],
)

signals_entered_pipeline = Counter(
    "karsa_signals_entered_pipeline_total",
    "Signals entering the full 6-stage pipeline",
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
