# ADR-007: Trust Nothing Startup Reconciliation

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team
**Classification:** SAFETY-CRITICAL

---

## Context

After any crash, Docker restart, or unexpected shutdown, the local PostgreSQL database and in-memory state may be out of sync with Bybit's actual positions and orders. Trusting stale local state could lead to ghost positions (bot thinks it has a position, Bybit says flat) or orphaned orders (Bybit has open orders the bot doesn't know about).

---

## Decision

**On every startup, the system must not trust the local database.** It must execute a "Trust Nothing" reconciliation sequence that treats Bybit's REST API as ground truth.

---

## Reconciliation Sequence

1. **Fetch Exchange Truth:** Query Bybit REST API for all actual open positions and active orders.
2. **Fetch Local Truth:** Query local PostgreSQL for last known state.
3. **Compare & Resolve:**
   - **Scenario A (Clean):** Match perfectly → proceed to normal startup.
   - **Scenario B (Orphaned Orders):** Bybit has orders Postgres doesn't know → cancel them immediately.
   - **Scenario C (Ghost Positions):** Postgres says position, Bybit says flat → overwrite Postgres with Bybit truth, log CRITICAL.
   - **Scenario D (Postgres Dead):** DB connection fails → create fresh schema from Bybit state, proceed.
4. **Sync Complete:** Only after reconciliation does the Watchdog give "Green Light" to Alpha Bridge.

---

## Consequences

### Positive
- **Crash safety:** System can safely resume after any failure without manual intervention.
- **No ghost positions:** Eliminates the risk of the bot "managing" positions that don't exist on the exchange.
- **No orphaned orders:** Cancels any orders the bot doesn't know about, preventing unexpected fills.

### Negative
- **Startup latency:** Reconciliation adds 1-5 seconds to startup (depends on Bybit API response time).
- **Bybit API dependency:** If Bybit is unreachable during startup, reconciliation degrades (Scenario D). Bot proceeds in degraded mode.

---

## Implementation

- `app/core/state.py:reconcile()` — startup reconciliation logic
- `RISK_AND_RUNBOOK.md` §4 — "The Trust Nothing Startup Protocol"
- `DATA_MODEL.md` §6 — "State Reconciliation Mapping"

---

## References

- `CONTEXT.md` §4: "'Trust nothing' startup reconciliation"
- `RISK_AND_RUNBOOK.md` §4: "Disaster Recovery & State Reconciliation"
- `DEFINITION_OF_DONE.md` §3.E: "Startup Reconciliation"
