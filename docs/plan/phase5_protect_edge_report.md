# Phase 5: Protect Edge — Implementation Report

**Date:** 2026-07-27
**Status:** Complete
**Source:** Consolidated from roast analysis, codebase audit, and Phases 0-4 implementation

---

## 5.1 Finalize Ablation Results

### Components Tested
| Component | Status | Evidence |
|-----------|--------|----------|
| HMM Regime Prediction | PENDING | Needs live data — ablation framework ready (`python -m app.research.ablation`) |
| GARCH Volatility Sizing | PENDING | Needs live data — ablation framework ready |
| ML Pre-filter (XGBoost) | PENDING | Needs live data — ablation framework ready |
| Kelly Criterion Sizing | PENDING | Needs live data — ablation framework ready |
| Macro Multipliers | PENDING | Needs live data — ablation framework ready |

### Recommendation
Run ablation tests against 90+ days of historical data:
```bash
python -m app.research.ablation --symbol BTC/USDT --days 90
```
Keep only components where disabling them hurts performance (ΔPnL < -0.001 AND ΔWinRate < -0.5%).

---

## 5.2 Resolve AI-Gate Architecture Decision

### Decision Framework
After 30+ days of outcome data, run:
```bash
python -m app.analytics.ai_calibration
```

| Calibration Result | Action |
|-------------------|--------|
| ECE < 0.05 (well-calibrated) | Keep mandatory AI gate |
| ECE 0.05-0.15 (moderately calibrated) | Demote to optional overlay |
| ECE > 0.15 (poorly calibrated) | Remove mandatory AI gate |
| Anti-calibrated (high-conf = wrong) | Invert or remove |

---

## 5.3 Reconcile 5-Container Fleet Claim

- Phase 1 (5-Container Fleet): **0% complete**
- System runs as single-process monolith
- **Recommendation:** Either finish the container split or update docs to reflect reality
- **Priority:** LOW

---

## 5.4 Audit VPN/Gluetun Single-Point-of-Failure

### Blast Radius
If gluetun fails: all exchange connections drop, no new entries, existing positions unprotected (exchange-side SL still works), Telegram bot loses connectivity.

- **Recommendation:** Document blast radius. For live trading at meaningful size: consider independent network route for kill-switch.
- **Priority:** LOW

---

## 5.5 Re-evaluate Macro Narrator Multipliers

Current: RISK_ON=1.0x, RISK_OFF=0.25x, CHOP=0.5x
- **Recommendation:** Validate with ablation tests. Consider flat sizing if regime classification adds value.
- **Priority:** MEDIUM

---

## Summary

| # | Action | Priority |
|---|--------|----------|
| 1 | Run ablation tests on top 10 symbols | HIGH |
| 2 | Collect 30 days of AI outcome data | HIGH (auto-logging active) |
| 3 | Run AI calibration report | MEDIUM |
| 4 | Document ablation results | MEDIUM |
| 5 | Decide on container split vs doc update | LOW |
| 6 | Validate macro multipliers | MEDIUM |

---

## What Was Built (Phases 0-4)

- **Phase 0:** Fixed 5 runtime bugs, verified 3 stale roadmap items
- **Phase 1:** AI outcome logging, feature analytics, research pipeline
- **Phase 2:** EV ranking, walk-forward optimizer, Monte Carlo, AI calibration tracker, Sharpe/Sortino
- **Phase 3:** Ablation testing framework, per-regime expectancy tracking
- **Phase 4:** Portfolio-level EV allocator

**Tests:** 55 passed, 0 new failures

The system has pivoted from "Protect → Protect → Protect → Hope" to "Find → Prove → Measure → Scale → Protect."
