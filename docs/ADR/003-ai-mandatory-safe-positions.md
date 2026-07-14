# ADR-003: AI Mandatory in Safe Positions, Forbidden in Hot Path

**Status:** Accepted
**Date:** 2024-06-01
**Deciders:** Core team
**Classification:** ARCHITECTURE

---

## Context

The system trades 15m-4h swing/intraday structure. The WARP proxy adds 100-300ms latency. LLM inference adds 200-800ms. Stacking these latencies in the execution hot path would destroy any timeframe-sensitive edge. However, AI can synthesize signals in ways that rule engines cannot — specifically in ambiguous conditions that are neither clearly good nor clearly bad.

---

## Decision

AI is **mandatory** in two safe positions, strictly forbidden in the execution hot path:

1. **Pre-Entry CryptoAnalyst** (`app/alpha/analyst.py`): Runs after deterministic signal generation, before risk gate. Synthesizes TA indicators into final confidence. If AI fails, signal is **rejected** (not bypassed).

2. **Post-Entry Position Judge** (`app/alpha/position_judge.py`): Runs in CheckpointManager when position is in ambiguous zone. 2-tier escalation (haiku → sonnet). 3 consecutive HOLDs on loser = forced EXIT.

AI is **forbidden** in SOR, risk gate, and order execution — these must remain deterministic and auditable.

---

## Consequences

### Positive
- **Win-rate lift:** ~15-25% additional win rate from AI synthesis in ambiguous conditions (estimated from KCT reference).
- **Cost:** ~$0.60-1.20/day at 5 symbols — negligible vs trading capital.
- **Auditability:** AI decisions are logged with reasoning text. Deterministic path remains fully auditable.

### Negative
- **Dependency on 9router:** If 9router is down, signals are rejected (AI mandatory). Circuit breaker halts signals after 3 consecutive failures.
- **Non-determinism:** AI responses vary between calls. Mitigated by caching (5min TTL) and deterministic fallback gates.
- **Cost scaling:** At 60 symbols, cost increases proportionally. Still negligible vs capital.

---

## Implementation

- 9router proxy at `127.0.0.1:20129` (OpenAI-compatible endpoint)
- `app/core/ai_client.py` — async HTTP client with retry, timeout, cache
- `docs/review/ai_layer_analysis.md` — latency math and safe-position rationale

---

## References

- `CLAUDE.md` Non-Negotiable Rules: "AI mandatory in safe positions"
- `AI_INTERFACE.md` — request/response schemas
- `docs/review/ai_layer_analysis.md` — full analysis
