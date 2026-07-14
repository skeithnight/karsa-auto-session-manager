# ADR-005: Multi-Exchange Read, Local Execute

**Status:** Accepted
**Date:** 2024-01-01
**Deciders:** Core team
**Classification:** ARCHITECTURE

---

## Context

Bybit requires a WARP SOCKS5 proxy due to geo-restrictions. Multi-venue execution would compound proxy latency and create multi-venue state synchronization challenges. However, reading from multiple exchanges provides a structural alpha advantage — cross-exchange VWAP and lead-lag signals that single-exchange systems cannot replicate.

---

## Decision

**Read from multiple exchanges (Binance, OKX, Bybit) via CCXT Pro WebSocket. Execute trades only on Bybit.** The "Read Global, Execute Local" paradigm.

---

## Consequences

### Positive
- **Structural alpha:** Cross-exchange VWAP, aggregate skew, and lead-lag (Binance leads, Bybit lags 15-30s) are unique signals unavailable to single-exchange systems.
- **Liquidity picture:** Aggregate volume across 3 exchanges gives more accurate liquidity assessment than any single venue.
- **Reduced proxy dependency:** Only Bybit traffic goes through WARP. Binance/OKX data comes directly.

### Negative
- **Complexity:** Must normalize disparate exchange schemas into unified `GlobalState`.
- **Stale data risk:** If one exchange's WebSocket drops, the system must gracefully degrade (mark as `STALE`, exclude from calculations).
- **No cross-exchange arbitrage:** The system captures directional alpha, not price discrepancies between venues.

---

## Implementation

- `app/data/ccxt_manager.py` — manages WebSocket connections to all 3 exchanges
- `app/data/normalizer.py` — normalizes exchange-specific schemas into `ExchangeData` / `GlobalState`
- `app/data/filters.py` — bad tick rejection (>5% in <1s)
- `app/alpha/lead_lag_buffer.py` — 15-min rolling price buffer for Binance vs Bybit comparison

---

## References

- `CONTEXT.md` §2: "Read Global, Execute Local"
- `ARCHITECTURE.md` §1: Architectural Philosophy
- `PRD.md` §3: Core Thesis
