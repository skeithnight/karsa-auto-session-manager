# ADR-006: Swing/Intraday Timeframe Over HFT

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team
**Classification:** STRATEGY

---

## Context

The WARP SOCKS5 proxy adds 100-300ms to every Bybit API call. Millisecond-level scalping through that latency is a guaranteed loser — market makers and co-located HFT firms will front-run every order. The system needs a timeframe where proxy latency is mathematically irrelevant to the trade's profitability.

---

## Decision

**Trade 15m-4h swing/intraday structure instead of HFT.** Capture structural and macro inefficiencies (funding rate divergences, order book imbalances, lead-lag trends) that take minutes or hours to play out.

---

## Consequences

### Positive
- **Latency irrelevant:** 200ms proxy latency on a 4-hour trade is noise (<0.001% of hold time).
- **Deeper alpha:** Structural inefficiencies (funding, skew, lead-lag) are more predictable than microsecond price movements.
- **Lower infrastructure cost:** No need for co-location, dedicated servers, or ultra-low-latency networking.

### Negative
- **Fewer opportunities:** Swing trades fire less frequently than HFT scalps. Acceptable for single-operator system.
- **Larger position sizes:** Each trade carries more risk per position. Mitigated by exchange-side SL and circuit breakers.
- **Overnight risk:** Positions held for hours are exposed to gap risk. Mitigated by 72h TIME_STOP and checkpoint system.

---

## Implementation

- Signal generation runs every 1-5 seconds (not millisecond-level)
- Position hold time: 15 minutes to 72 hours
- Checkpoint system evaluates every 5 minutes
- Trailing stop runs every 60 seconds

---

## References

- `CONTEXT.md` §4: "Swing/Intraday (15m-4h), not HFT"
- `PRD.md` §3: "The Strategic Pivot"
- `ARCHITECTURE.md` §1: "proxy latency is mathematically irrelevant to the alpha"
