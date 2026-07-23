# Metrics Dictionary
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed, rev. 2 (added Shadow Mode + Pipeline Funnel metrics, WARP -> gluetun)
**Purpose:** One canonical list of every Prometheus metric the system exposes, so naming doesn't drift as components get built independently.

---

## Naming Convention Flag (Doc Conflict)

`DEFINITION_OF_DONE.md` §4 gives examples like `karsa_alpha_signals_generated_total` and `karsa_bybit_order_latency_seconds` (prefixed with `karsa_`). `MVP_SCOPE.md` §3.E lists `orders_placed_total`, `order_latency_seconds`, `websocket_disconnects_total` (unprefixed). These likely describe the same underlying metrics with inconsistent naming — worth adding as an item to `CONTEXT.md`'s open issues.

**This dictionary adopts the `karsa_` prefix as canonical**, following standard Prometheus convention (`<namespace>_<subsystem>_<name>_<unit>`) and matching `DEFINITION_OF_DONE.md`'s explicit examples, since that's the doc that actually defines the DoD gate ("New features must expose relevant metrics"). Treat the `MVP_SCOPE.md` names as the pre-convention drafts they superseded.

---

## 1. Key 1 — Global Data Engine

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_websocket_disconnects_total` | Counter | `exchange` | Count of WS disconnect events, per exchange |
| `karsa_exchange_status` | Gauge | `exchange` | `0`=ACTIVE, `1`=STALE, `2`=DEGRADED — current per-exchange feed status |
| `karsa_bad_ticks_rejected_total` | Counter | `exchange`, `symbol` | Count of ticks rejected by the >5%/<1s filter |
| `karsa_ws_heartbeat_age_seconds` | Gauge | `exchange` | Seconds since last received WS message |

## 2. Key 2 — Alpha Bridge

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_alpha_signals_generated_total` | Counter | `symbol`, `direction` | Every raw signal generated (LONG/SHORT/FLAT), regardless of risk outcome — named verbatim in `DEFINITION_OF_DONE.md` §4 |
| `karsa_alpha_calc_duration_seconds` | Histogram | `symbol` | Time spent computing VWAP/Skew/Lead-Lag per cycle — must stay under 5ms per `DEFINITION_OF_DONE.md` §3.B |
| `karsa_alpha_confidence_score` | Gauge | `symbol` | Latest `TradingSignal.confidence_score` value |

## 3. Key 3 — 3-Layer Risk Gate

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_risk_gate_evaluations_total` | Counter | `gate_name`, `result` | Every gate evaluation (liquidity / spread_health / circuit_breaker) and its pass/fail outcome |
| `karsa_risk_gate_rejections_total` | Counter | `gate_name`, `reason` | Signals blocked, broken out by which gate and why |
| `karsa_circuit_breaker_triggered_total` | Counter | `breaker_name` | Fires whenever a breaker from `RISK_AND_RUNBOOK.md` §2 trips: `daily_drawdown`, `consecutive_losses`, `latency_spike`, `margin_utilization`, `stale_data` |
| `karsa_daily_drawdown_pct` | Gauge | — | Current realized+unrealized PnL as % of starting daily equity — **the exact trigger threshold for this is disputed between docs; see `CONTEXT.md` Open Issue #2** |
| `karsa_margin_utilization_pct` | Gauge | — | Current Bybit margin used as % of account equity |

## 4. Key 4 — Bybit Executor

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_orders_placed_total` | Counter | `symbol`, `side`, `order_type` | Every order sent to Bybit — canonicalized from `MVP_SCOPE.md`'s `orders_placed_total` |
| `karsa_bybit_order_latency_seconds` | Histogram | `symbol` | Signal-generated → fill-confirmed latency — named verbatim in `DEFINITION_OF_DONE.md` §4; MVP success criteria requires p50 < 800ms |
| `karsa_sor_step_reached_total` | Counter | `symbol`, `step` (`post_only`\|`reprice`\|`market`) | Which SOR step ultimately filled the order — signals proxy/liquidity health over time |
| `karsa_stop_loss_placement_total` | Counter | `symbol`, `result` (`success`\|`failed`) | Every attempt to place the mandatory exchange-side SL on fill — a `failed` value here is a P0 alert, not a metric to shrug at |
| `karsa_partial_fills_total` | Counter | `symbol` | Count of partial fills requiring sync handling |

## 5. Key 5 — State Manager

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_reconciliation_events_total` | Counter | `scenario` (`clean`\|`orphaned_orders`\|`ghost_positions`\|`postgres_dead`) | Startup reconciliation outcome, matching the 4 scenarios in `RISK_AND_RUNBOOK.md` §4 |
| `karsa_postgres_write_errors_total` | Counter | `table` | Failed writes to `trades`/`signals`/`system_events` |
| `karsa_state_divergence_detected_total` | Counter | — | Fires on Scenario C (Ghost Positions) — this should page a human, not just increment quietly |
| `karsa_trade_reconcile_discrepancies` | Counter | `kind` | Count of discrepancies found during reconciliation |
| `karsa_trade_reconcile_repairs` | Counter | `kind` | Count of successful state repairs made |
| `karsa_reconciler_stale_removed` | Counter | `symbol` | Count of stale keys cleaned up |

## 6. Key 6 — Watchdog & Telemetry

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_dead_mans_switch_ping_total` | Counter | `result` (`success`\|`failed`) | Every external ping attempt (Healthchecks.io / Telegram) |
| `karsa_event_loop_lag_seconds` | Gauge | — | Measured `asyncio` loop blocking delay — Watchdog acts if this exceeds 100ms |
| `karsa_kill_switch_triggered_total` | Counter | `trigger_source` (`telegram`\|`file_flag`\|`sigterm`\|`sigint`) | Every kill switch activation and its source |
| `karsa_proxy_latency_ms` | Histogram | — | VPN tunnel (gluetun) round-trip latency, sampled independently of order latency, used for the >2000ms failover trigger in `RISK_AND_RUNBOOK.md` §3 |
| `karsa_memory_usage_bytes` | Gauge | — | Process RSS, checked against Docker memory limits |

---

## 7. Stage 1 — Universe Selection

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_universe_refresh_total` | Counter | — | Universe scorer refresh cycles completed |
| `karsa_universe_symbols_active` | Gauge | — | Current number of active tradeable symbols |
| `karsa_universe_score` | Gauge | `symbol` | Last computed universe score per symbol |
| `karsa_universe_sector_cap_rejections_total` | Counter | `sector` | Signals rejected because sector already at cap |

## 8. Stage 3 — AI Signal Generation

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_ai_analyst_calls_total` | Counter | `result` (`success`/`failure`/`timeout`/`parse_error`) | Every AI analyst invocation and outcome |
| `karsa_ai_analyst_latency_seconds` | Histogram | — | 9router call latency distribution |
| `karsa_ai_analyst_confidence` | Histogram | — | AI confidence distribution (0-100) |
| `karsa_ai_analyst_rejections_total` | Counter | `reason` | Signals rejected by AI (below threshold, AI failed, AI says FLAT) |
| `karsa_ai_analyst_cache_hits_total` | Counter | — | Cache hits from `ai:cache:*` Redis keys |
| `karsa_multi_tf_penalty_applied_total` | Counter | `symbol` | Times 4H trend contradicted 1H signal |
| `karsa_final_confidence_score` | Gauge | `symbol` | Final blended confidence (quant * 0.5 + AI * 0.5) |

## 9. Stage 6 — AI Position Judge

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_position_judge_calls_total` | Counter | `tier`, `action` | Every position judge invocation |
| `karsa_position_judge_latency_seconds` | Histogram | `tier` | Judge call latency by tier |
| `karsa_position_judge_consecutive_hold_exits_total` | Counter | — | Forced exits after 3 consecutive HOLDs |
| `karsa_trade_memory_entries_stored_total` | Counter | `symbol` | Trade memory entries written on close |
| `karsa_trade_memory_injection_hits_total` | Counter | `symbol` | Memory context injected into AI prompt |

## 10. Lifecycle Integration

| Metric | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `karsa_signals_entered_pipeline_total` | Counter | `symbol` | Signals entering the full 6-stage pipeline |
| `karsa_signals_completed_pipeline_total` | Counter | `symbol`, `outcome` | Signals completed or rejected at stage |
| `karsa_position_lifecycle_duration_seconds` | Histogram | — | Time from position open to close |
| `karsa_risk_gate_reject` | Counter | `symbol`, `reason` | Signals rejected by risk gate |
| `karsa_risk_gate_pass` | Counter | `symbol` | Signals passed by risk gate |
| `karsa_positions_opened` | Counter | `symbol`, `side` | Positions successfully opened |
| `karsa_positions_closed` | Counter | `symbol`, `side`, `exit_reason` | Positions closed and why |

---

## 11. Shadow Mode Metrics (Phase 3.1 -- Built)

| Metric | Type | Labels | Description |
|:---|:---|:---|:---|
| `karsa_shadow_mode_active` | Gauge | — | 1 when shadow mode is active, 0 otherwise |
| `karsa_shadow_orders_placed_total` | Counter | `symbol`, `side` | Shadow virtual orders placed |
| `karsa_shadow_exits_placed_total` | Counter | `symbol`, `reason` | Shadow virtual exits placed |
| `karsa_shadow_pnl_usdt` | Histogram | — | Shadow virtual PnL in USDT per closed trade |
| `karsa_shadow_fees_total_usdt` | Counter | — | Cumulative shadow trading fees in USDT |
| `karsa_shadow_slippage_total_usdt` | Counter | — | Cumulative shadow slippage cost in USDT |
| `karsa_shadow_positions_open` | Gauge | — | Currently open shadow positions |
| `karsa_shadow_sl_hits_total` | Counter | `symbol`, `side` | Shadow stop-loss hits triggered |
| `karsa_shadow_funding_fees_total_usdt` | Counter | — | Cumulative shadow funding rate fees in USDT |
| `karsa_shadow_limit_orders_unfilled_total` | Counter | `symbol` | Shadow post-only limit orders that expired unfilled (TTL 600s) |
| `karsa_shadow_live_entry_divergence_seconds` | Histogram | — | Shadow vs Live entry divergence in seconds |
| `karsa_shadow_live_slippage_bps` | Histogram | — | Shadow vs Live slippage comparison in basis points |

---

## 12. Pipeline Funnel Metrics (Phase 6 -- Built)

| Metric | Type | Labels | Description |
|:---|:---|:---|:---|
| `karsa_regime_classified_total` | Counter | `symbol`, `regime` | Signals classified by regime |
| `karsa_strategy_scored_total` | Counter | `regime`, `score_bucket` | Signals scored by strategy router |
| `karsa_signal_confidence_passed_total` | Counter | `regime` | Signals that passed confidence threshold |
| `karsa_signal_killed_total` | Counter | `stage`, `reason` | Signals killed at each pipeline stage |
| `karsa_strategy_score` | Histogram | `symbol`, `regime` | Strategy router score distribution |
| `karsa_signal_confidence` | Histogram | — | Final signal confidence distribution |

---

## 13. Dashboards vs. Raw Metrics

Per `MVP_SCOPE.md` §4, Grafana is out of scope for MVP — most metrics are queried raw via `/metrics` or `curl`'d directly. One dashboard does exist: `grafana/dashboards/asm-pipeline-funnel.json` covers the Phase 6 pipeline funnel metrics. Additional dashboards should be added there as new metric sections land (see `IDEAS_BACKLOG.md` #7).

---

## 14. Adding a New Metric

Before adding anything not in this table: (1) confirm it maps to a real DoD or Runbook requirement — don't add speculative metrics, (2) follow the `karsa_<component>_<measurement>_<unit>` convention, (3) update this file in the same PR that adds the metric, so this dictionary never drifts out of sync with the code the way the original docs drifted from each other.