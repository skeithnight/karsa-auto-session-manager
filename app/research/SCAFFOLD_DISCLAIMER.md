# Research Module Scaffold Status

⚠️  **SCAFFOLD MODULES** — Per quant trader persona review recommendation #9:
"Remove research theater"

These modules are classified as **SCAFFOLD** until they meet production criteria.

---

## Current Status

| Module | Status | Issue |
|--------|--------|-------|
| `experiment_runner.py` | ❌ **SCAFFOLD** | Uses mock trades, not real backtest path |
| `ablation.py` | ⚠️ **PARTIAL** | Structure exists, needs real integration |
| `metrics_engine.py` | ✅ **PRODUCTION** | Used by ranking engine |
| `ranking_engine.py` | ✅ **PRODUCTION** | Used by live loop |
| `statistical_validator.py` | ⚠️ **PARTIAL** | Structure exists, needs validation |
| `feature_analytics.py` | ⚠️ **PARTIAL** | Structure exists, needs real data |

---

## Production Criteria

A research module is promoted from SCAFFOLD to PRODUCTION when:

1. **Real data integration** — Uses actual backtest engine, not mocks
2. **Validation** — Produces statistically significant results
3. **Promotion path** — Can promote strategies to live trading
4. **Ablation support** — Can genuinely disable components
5. **Machine-readable output** — Results can be consumed by ranking engine

---

## How to Promote

1. Replace mock trades with real BacktestEngine calls
2. Validate results against historical data
3. Ensure ablation genuinely disables components
4. Integrate with ranking engine for promotion decisions
5. Remove this disclaimer when production criteria are met

---

## Usage

These modules can be used for:
- **Exploration** — Understanding research patterns
- **Prototyping** — Testing new ideas quickly
- **Documentation** — Showing research intent

They should NOT be used for:
- **Live trading decisions** — Not production-grade
- **Strategy promotion** — Not validated
- **Capital allocation** — Not statistically significant

---

**Last updated:** 2026-07-28
**Reviewer:** Quant Trader Persona Review
**Recommendation:** #9 — Remove research theater
