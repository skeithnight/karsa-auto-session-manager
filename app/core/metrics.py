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
