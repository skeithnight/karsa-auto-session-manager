# ADR-010: Hybrid Intelligence Trading System (CEX + DEX + DeFi Yield)

**Status:** Accepted
**Date:** 2026-08-07
**Deciders:** Core team

---

## Context

KASM v2.1 operates as a single-venue Order Taker on Bybit. In the 2026 trading landscape, CEX-only perpetuals bots face shrinking edges, uncaptured Loss-Versus-Rebalancing (LVR) arbitrage opportunities on DEXs, and idle capital drag (0% yield on cash balances during dry market regimes).

---

## Decision

Expand KASM from a CEX-only bot (v2.1) into a **Hybrid Intelligence Trading System (v3.0)** across a 6-layer architecture:

1. **Layer 1 (Sensorium):** Ingest real-time DEX pool events (Uniswap v3/v4) and gas telemetry alongside existing CEX WebSockets.
2. **Layer 2 (Brain):** Add LVR Sniper calculations and gas-adjusted EV scoring.
3. **Layer 3 (Vault):** Automate active treasury management (idle USDC routed to Pendle PT / HLP Vault yield) with whitelist-gated PRM risk checks.
4. **Layer 4 (Hands):** Multi-venue execution via Hyperliquid API and MEV-shielded EVM routing (Flashbots Protect).
5. **Layer 5 (Maker):** On-chain dynamic liquidity engineering via Uniswap v4 Hooks.
6. **Layer 6 (Watcher):** Enhanced telemetry and AI analyst context.

This decision supersedes the strict "Bybit-Only Execution" and "No Multi-Exchange Execution" constraints of early MVP phases once Phase 1 stability and graduation gates pass.

---

## Consequences

### Positive
- **New Alpha Sources:** Captures DEX-CEX latency arbitrage (LVR) and extracts value from stale DEX ticks.
- **Treasury Efficiency:** Converts idle cash reserves into predictable fixed yield (Pendle PT) or market-making yield (HLP).
- **Execution Flexibility:** Lower latency / zero gas fees on Hyperliquid for high-frequency mean reversion setups.
- **LVR Recapture:** Uniswap v4 dynamic fee hooks penalize toxic arbitrage flow during high volatility regimes.

### Negative
- **Expanded Attack Surface:** Introduces smart contract risk, oracle manipulation risks, and gas cost drag.
- **Higher Operational Complexity:** Requires mainnet fork testing, EVM node maintenance, and Flashbots private RPC routing.

---

## Mitigations & Non-Negotiable Boundaries

1. **Public Mempool Ban:** All EVM transactions MUST use Flashbots Protect / MEV Blocker bundles.
2. **DeFi Whitelist Gate:** Smart contract interactions are strictly limited to audited, whitelisted protocols.
3. **Failure Isolation:** EVM network drops must never disrupt or block the primary CEX trading pipeline.
4. **Decimal Math Rule:** All financial, yield, gas, and sizing math must remain strictly `decimal.Decimal`.

---

## References

- `docs/plan/refinement_defi.md`
- `docs/plan/refinement_defi_analysis.md`
- `docs/plan/implementation_plan_defi_v3.md`
- `MVP_SCOPE.md` §8
- `ARCHITECTURE.md` §4 Layer 5
