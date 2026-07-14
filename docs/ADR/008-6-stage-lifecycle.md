# ADR-008: 6-Stage Trade Lifecycle

**Status:** Accepted
**Date:** 2024-06-01
**Deciders:** Core team
**Classification:** ARCHITECTURE

---

## Context

The original ASM design used a simple 4-stage pipeline (Data → Alpha → Risk → Execution) with optional AI. KCT (karsa-claude-trading) demonstrated that a 6-stage pipeline with mandatory AI, dynamic universe selection, and structured post-entry management produces significantly higher win rates. ASM needs to evolve from a rule-based threshold engine to an AI-augmented regime-adaptive system while preserving its multi-exchange data advantage.

---

## Decision

Adopt a **6-stage trade lifecycle** that matches KCT's architecture, enhanced with ASM's multi-exchange data (Binance + OKX + Bybit):

1. **Universe Selection** — Dynamic scoring (Volume + Momentum + Squeeze + Overextension). Top 15 symbols, sector diversity cap.
2. **Regime Detection** — Hurst + ADX + EMA200 on BTC 1H. CHOP halts all trading.
3. **Signal Generation (AI-Mandatory)** — Multi-signal composite + entry filter + multi-TF confirmation + AI CryptoAnalyst. Final confidence = quant × 0.5 + AI × 0.5.
4. **Risk Gate (Deterministic)** — 3-layer gate + sector cap. No AI in this path.
5. **SOR Execution (Deterministic)** — Post-Only → Reprice → Market + exchange-side SL. No AI in this path.
6. **Post-Entry Management (AI-Mandatory)** — Trailing stop + checkpoints + AI Position Judge + trade memory.

---

## Consequences

### Positive
- **KCT parity:** Brings ASM to feature parity with KCT's proven win-rate architecture.
- **Multi-exchange edge preserved:** Universe scoring uses cross-exchange aggregate volume — KCT cannot replicate this.
- **Structured post-entry:** Positions are actively managed with trailing stops, checkpoints, and AI judge — no more "open and forget."
- **Trade memory:** AI learns from past trades (same symbol + regime), improving over time.

### Negative
- **Complexity:** 6 stages with 2 mandatory AI calls increase system complexity. Mitigated by clear stage boundaries and ownership rules.
- **AI dependency:** If 9router is down, signals are rejected (mandatory). Circuit breaker halts after 3 failures.
- **More components to test:** Each stage needs unit tests, integration tests, and the full pipeline needs end-to-end tests.

---

## Implementation

- New files: `universe_scorer.py`, `sector_mapping.py`, `multi_tf.py`, `trade_memory.py`, `sector_cap.py`
- Updated files: `main.py` (executor wiring), `signals.py` (multi-TF integration), `analyst.py` (trade memory injection)
- See `MVP_SCOPE.md` §7 Phase 4.5 for implementation plan

---

## References

- `docs/review/workflow_benchmark.md` — ASM vs KCT gap analysis
- `docs/review/ai_layer_analysis.md` — AI safe-position rationale
- `ARCHITECTURE.md` §5 — Full 6-stage lifecycle specification
- `PRD.md` §6 — Key System Requirements (6-Stage)
