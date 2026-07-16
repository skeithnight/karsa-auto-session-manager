# Pipeline Funnel Dashboard

## Implemented

### New Metrics (app/core/metrics.py)

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `karsa_regime_classified_total` | Counter | `regime` | Flow count per regime classification |
| `karsa_strategy_scored_total` | Counter | `regime`, `score_bucket` | Stage counter (0-50, 50-65, 65-85, 85-100) |
| `karsa_signal_confidence_passed_total` | Counter | `regime` | Signals passing confidence gate |
| `karsa_signals_killed_total` | Counter | `stage`, `reason` | Kill breakdown by stage + reason |

### Reused Existing Metrics

| Metric | Stage | Labels |
|---|---|---|
| `karsa_risk_gate_pass_total` | Risk gate pass | `symbol` |
| `karsa_risk_gate_reject_total` | Risk gate reject | `symbol`, `reason` |
| `karsa_orders_placed_total` | Execution | `symbol`, `side` |
| `karsa_signal_confidence` | Confidence distribution | `symbol` (histogram) |
| `karsa_strategy_score` | Strategy score distribution | `symbol`, `regime` (histogram) |

### Instrumentation Points

| File | Method | Metric Added |
|---|---|---|
| `app/alpha/regime_classifier.py:89` | `classify()` | `regime_classified_total` |
| `app/alpha/strategy_router.py:95` | `evaluate_signal()` | `strategy_scored_total` |
| `app/risk/gates.py:97` | `evaluate()` | `risk_gate_pass/reject` + `signals_killed_total` |
| `app/alpha/signals.py:141,153` | `generate()` | `signals_killed_total` (lead-lag kill) |
| `app/alpha/signals.py:222` | `generate()` | `signals_killed_total` (low confidence/flat) |
| `app/alpha/signals.py:226` | `generate()` | `signal_confidence_passed_total` |

### Grafana Dashboard

`grafana/dashboards/asm-pipeline-funnel.json` — 15 panels:

1. **Signal Funnel (24h)** — bargauge showing 5-stage funnel
2. **Kill Reasons** — piechart donut of kill reasons
3. **Kill Stage Breakdown** — bargauge by stage
4. **Risk Gate Pass vs Reject** — stat
5. **Kill Reasons Detail** — table with stage + reason
6. **CHOP Funnel** — stat per-regime funnel
7. **TREND_BULL Funnel** — stat per-regime funnel
8. **RANGE Funnel** — stat per-regime funnel
9. **Strategy Score Distribution** — histogram
10. **Signal Confidence Distribution** — histogram
11. **Score Buckets by Regime** — bargauge
12. **Confidence Pass Rate by Regime** — stat (ratio)
13. **Current Regime** — stat
14. **ADX / Hurst** — stat
15. **Regime Classifications (5m)** — timeseries

### Kill Reason Values (actual code)

| Stage | Reason | Source |
|---|---|---|
| `signal_gen` | `lead_lag_kill` | `signals.py` lead-lag hard kill |
| `confidence_gate` | `low_confidence` | `signals.py` confidence < threshold |
| `confidence_gate` | `flat_direction` | `signals.py` direction == FLAT |
| `risk_gate` | `order_notional` | `gates.py` dust trade rejection |
| `risk_gate` | `liquidity` | `gates.py` L1 depth too low |
| `risk_gate` | `spread_health` | `gates.py` bid-ask spread too wide |
| `risk_gate` | `circuit_breaker` | `gates.py` daily PnL drawdown halted |

### Validation

- ruff: clean
- mypy: 3 pre-existing errors only
- tests: 20/20 signal tests pass
