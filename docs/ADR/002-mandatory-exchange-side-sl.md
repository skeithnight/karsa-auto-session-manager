# ADR-002: Mandatory Exchange-Side Stop-Loss

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team
**Classification:** SAFETY-CRITICAL

---

## Context

The bot runs autonomously and depends on a WARP SOCKS5 proxy for Bybit access. If the bot process crashes, the proxy drops, or the server loses power, in-memory stop-losses are worthless. Open positions would be unprotected.

---

## Decision

**Every position fill must immediately place a hard Stop-Loss order on the Bybit exchange server.** This is the single most safety-critical line of code in the repo. The bot's internal "soft" stop-loss is secondary.

---

## Consequences

### Positive
- **Crash resilience:** If the bot dies, the exchange-side SL protects capital. The position will be closed by Bybit's server-side logic.
- **Proxy resilience:** If the WARP proxy drops, the SL is already resting on Bybit's servers — it doesn't need the proxy to execute.
- **Operator confidence:** The operator can sleep knowing positions are protected even if the bot freezes.

### Negative
- **API cost:** Each fill requires an additional API call to place the SL. Mitigated by Bybit's generous rate limits.
- **SL placement failure:** If the SL placement API call fails, the position is unprotected. The bot must retry and alert on failure.

---

## Implementation

- `app/execution/bybit_client.py:place_stop_loss()` — places conditional stop order on Bybit
- `app/execution/sor.py:_place_sl_after_fill()` — called immediately after every fill (Post-Only, Reprice, or Market)
- `app/execution/position_lifecycle.py:TrailingStopManager` — amends SL as price moves favorably

---

## References

- `CLAUDE.md` Non-Negotiable Rules: "Every position gets an exchange-side SL"
- `RISK_AND_RUNBOOK.md` §7: "Exchange-Side Stop Losses"
- `DEFINITION_OF_DONE.md` §3.D: "Places an exchange-side Stop-Loss immediately upon position fill"
