# ADR-004: Decimal for All Financial Math

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team
**Classification:** SAFETY-CRITICAL

---

## Context

Floating-point arithmetic introduces precision errors that are unacceptable for PnL-bearing calculations. A `float` representation of `0.1 + 0.2` yields `0.30000000000000004`, not `0.3`. In a trading system processing thousands of calculations daily, these errors accumulate and can cause incorrect PnL reporting, wrong position sizing, or incorrect stop-loss levels.

---

## Decision

**All prices, sizes, PnL, and financial calculations must use `decimal.Decimal`.** Using `float` for money is a non-negotiable rule violation.

---

## Consequences

### Positive
- **Precision:** `Decimal("0.1") + Decimal("0.2") == Decimal("0.3")` — exact arithmetic.
- **Auditability:** Financial reports match exchange records exactly.
- **Compliance:** Meets institutional-grade financial calculation standards.

### Negative
- **Performance:** `Decimal` operations are ~10x slower than `float`. Acceptable for 15m-4h timeframe (not HFT).
- **Serialization:** `Decimal` must be serialized as strings in JSON/Redis (`"64250.50"` not `64250.5`). Requires explicit conversion.
- **API compatibility:** Some libraries return `float`. Must convert with `Decimal(str(value))` — never `Decimal(float_value)` (preserves the float imprecision).

---

## Implementation

- `app/data/normalizer.py` — all normalized prices are `Decimal`
- `app/alpha/signals.py` — confidence scores are `Decimal`
- `app/risk/gates.py` — all thresholds are `Decimal`
- `app/execution/sor.py` — order prices and sizes are `Decimal`
- `DATA_MODEL.md` §1 — serialization rules mandate string representation

---

## References

- `CLAUDE.md` Non-Negotiable Rules: "No `float` for money"
- `DEFINITION_OF_DONE.md` §4: "Using `float` for money" is anti-pattern #1
- `CONTEXT.md` Issue #7: `daily_drawdown_limit` was `float`, now fixed to `Decimal`
