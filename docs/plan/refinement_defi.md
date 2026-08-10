As Lead Quant Architect, I am shifting gears from implementation to **pure system design and architectural blueprinting**.

To evolve **Karsa Auto Session Manager (KASM)** from a highly optimized CEX perpetuals bot (v2.1) into a true **Hybrid Intelligence Trading System (v3.0)**, we must fundamentally restructure the system's topology. We are moving from a single-venue *Order Taker* to a cross-venue *Liquidity Engineer and Arbitrageur*.

Here is the comprehensive **KASM v3.0 Architectural Blueprint**, mapping your existing 4-stage pipeline directly to the 2026 DeFi Alpha Handbook paradigms.

---

### I. The Paradigm Shift: CEX vs. Hybrid Topology

In v2.1, KASM assumes a frictionless, zero-gas environment where execution speed and EV scoring are the only edges. In v3.0, we introduce **Gas, MEV, Smart Contract Risk, and On-Chain Staleness** as first-class citizens in the pipeline.

| Architectural Domain | KASM v2.1 (CEX Native) | KASM v3.0 (Hybrid Intelligence) |
| :--- | :--- | :--- |
| **Universe Scan** | Volume, Momentum, Orderbook Skew | + TVL Velocity, Smart Money Flows, Mempool Density |
| **Signal Generation** | Statistical EV $\ge$ 0.55 | + LVR (Loss-Versus-Rebalancing) Capture, v4 Hook Alignment |
| **Risk Gate (PRM)** | Correlation, Sector Caps, Drawdown | + Smart Contract TVL Limits, Slashing Risk, Gas-Breakeven Math |
| **Execution** | Bybit Post-Only Maker | + Hyperliquid API, Flashbots/MEV Blocker Routing |
| **Idle Capital** | Cash (USDT) | + Pendle PT Yield, HLP Vault, Delta-Neutral Arb |

---

### II. The 6-Layer Hybrid Architecture Blueprint

We expand the 4-stage pipeline into a 6-layer modular architecture.

#### Layer 1: The Sensorium (Data & Universe)

*Expands `app/data/` to ingest non-CEX realities.*

* **Global State Engine (Existing):** CCXT Pro WS for Binance/OKX/Bybit.
* **On-Chain State Engine (New):**
  * *Mempool Listener:* Monitors pending transactions to detect incoming volatility before it hits the CEX order books.
  * *DeFi Telemetry:* Pulls TVL velocity from DeFiLlama and Smart Money clustering from Arkham/Nansen to weight the `universe_scorer.py`.
* **DEX Tick Tracker:** Subscribes to Uniswap v3/v4 pool events to track real-time `sqrtPriceX96` for LVR calculations.

#### Layer 2: The Brain (Alpha & Signal)

*Expands `app/alpha/` to synthesize cross-venue edges.*

* **LVR Sniper Module:** Compares CEX mid-price against DEX pool ticks. If the DEX is stale (due to gas friction or latency), it calculates the extractable LVR.
* **Regime Oracle Pusher:** Maps the existing `regime_classifier.py` (Hurst + ADX) into a standardized state machine that can be pushed to an on-chain Oracle (e.g., Chainlink Functions) to drive Uniswap v4 Hooks.
* **Composite EV Adjuster:** Deducts projected EVM gas costs and Flashbots priority fees from the raw EV score. An EV of 0.60 on a DEX might be 0.40 after gas (blocking the trade), while the same setup on Hyperliquid remains 0.58 (passing the gate).

#### Layer 3: The Vault (Risk & Treasury Management)

*Expands `app/risk/` to handle DeFi-specific tail risks and idle capital.*

* **Smart Contract Risk Gate:** Before approving any on-chain interaction, the PRM checks a localized registry of audited TVL, protocol age, and slashing risk (for LRTs).
* **Active Treasury Manager (ATM):** If the Quant EV Engine reports a "dry" market (no setups $\ge$ 0.55), the ATM automatically routes idle USDC into delta-neutral yield (Pendle PT, Hyperliquid HLP Vault, or Funding Rate Arb) rather than letting it sit dead.

#### Layer 4: The Hands (Execution & Routing)

*Expands `app/execution/` into a Multi-Venue Smart Order Router (SOR).*

* **CEX Router:** Bybit/Binance Post-Only Maker logic (existing).
* **Perp DEX Router:** Hyperliquid API integration. Utilizes HL's 200ms blocks and zero-gas environment for high-frequency mean-reversion setups.
* **EVM Spot Router (MEV-Shielded):** For DEX arbs or LP adjustments, transactions are bundled and routed *exclusively* through private RPCs (Flashbots Protect / MEV Blocker) to guarantee the "No Float / No Sandwich" invariant.

#### Layer 5: The Maker (On-Chain Liquidity Engineering)

*A completely new domain (`app/defi/`) for Uniswap v4 integration.*

* **KASM v4 Hook Controller:** KASM acts as the off-chain brain for a custom Uniswap v4 liquidity pool.
  * *Scenario:* KASM detects a "High Volatility / Trending" regime via ADX.
  * *Action:* KASM pushes a state update to the Hook's Oracle.
  * *Result:* The v4 Hook's `beforeSwap()` reads the Oracle and dynamically spikes the swap fee from 0.05% to 1.00%, extracting maximum value from toxic flow/arbitrageurs and protecting KASM's LP position.

#### Layer 6: The Watcher (Observability & AI)

*Expands `app/bot/` and `grafana/`.*

* **9Router AI Proxy Expansion:** The AI analyst is now prompted to evaluate not just chart patterns, but on-chain context (e.g., "Is there a massive token unlock on TokenTerminal?" or "Is Arkham showing exchange inflows?").
* **Funnel Metrics:** Grafana dashboards are updated to track `MEV_Captured_USD`, `Gas_Drag_Pct`, and `On_Chain_Setup_Conversion_Rate`.

---

### III. The Hybrid Strategy Matrix (Regime Mapping)

KASM’s greatest strength is its `regime_classifier.py`. In v3.0, we map these regimes directly to the strategies outlined in your DeFi Handbook.

| KASM Regime State | Math/Signal Profile | CEX Action (v2.1) | Hybrid DeFi Action (v3.0 Additions) |
| :--- | :--- | :--- | :--- |
| **RANGE (Low Vol)** | Hurst < 0.45, Low ADX | Mean Reversion, tight SL | **LP in Uniswap v4 Tight Range** (Collecting theta/fees). Route idle cash to **Pendle PT** for fixed yield. |
| **TREND (High Vol)** | Hurst > 0.55, High ADX | Momentum Breakouts | **Funding Rate Arb** (Short Perp / Long Spot). Spike **v4 Hook fees** to penalize arbs. |
| **PANIC (Extreme Vol)** | ADX > 60, Orderbook Skew | Halt Trading / APM Breakeven | **Delta-Neutral Stablecoin Yield** (e.g., sDAI). Withdraw all DEX LP to avoid max IL/LVR. |
| **INEFFICIENT (Stale)** | CEX moves, DEX lags | N/A (Blind spot in v2.1) | **LVR Sniping**: Buy DEX, sell CEX via Flashbots bundle. |

---

### IV. Threat Model & Security Posture

Moving on-chain introduces vectors that your `docs/DEFINITION_OF_DONE.md` must explicitly forbid or mitigate.

1. **The Public Mempool Death Sentence:**
    * *Rule:* KASM v3.0 **must never** broadcast an EVM transaction via a public RPC endpoint.
    * *Design:* The EVM Router must enforce a wrapper that intercepts all `send_raw_transaction` calls and redirects them to a Flashbots/MEV Blocker bundle builder.
2. **Smart Contract Cascading Risk:**
    * *Rule:* The Portfolio Risk Manager (PRM) must maintain a strict "DeFi Whitelist".
    * *Design:* Even if the EV is 0.90, if the target protocol (e.g., a new L2 DEX) is not in the audited whitelist, the PRM blocks the trade.
3. **Oracle Manipulation & Staleness:**
    * *Rule:* KASM must never trust a single DEX oracle for pricing.
    * *Design:* The LVR Sniper requires a consensus of $\ge$ 2 CEX order books (e.g., Binance + OKX mid-price) before triggering an on-chain arb, preventing flash-loan manipulation traps.
4. **Active Position Manager (APM) Gas Drain:**
    * *Rule:* The 2-second APM monitoring loop cannot trigger on-chain EVM transactions on every tick.
    * *Design:* APM remains purely off-chain (monitoring via WS). It only triggers an on-chain EVM transaction (e.g., pulling Uniswap v3 liquidity) when a hard threshold (e.g., Stop Loss hit or Regime Shift) is breached, and it batches the withdrawal to save gas.

---

### V. Rollout & Migration Strategy

To protect the live capital currently running on v2.1, the transition to v3.0 must follow a strict phased rollout:

* **Phase 1: The Shadow Oracle (Weeks 1-2)**
  * Deploy the On-Chain Data Ingestion and LVR Sniper in *read-only* mode.
  * Compare KASM's theoretical on-chain signals against actual CEX price action to calibrate the gas-adjusted EV thresholds.
* **Phase 2: Treasury Automation (Weeks 3-4)**
  * Enable the Active Treasury Manager.
  * Allow KASM to automatically sweep idle USDC into highly secure, blue-chip yield (e.g., Pendle PT-wstETH or Hyperliquid HLP) during low-EV periods.
* **Phase 3: Hyperliquid Execution (Weeks 5-6)**
  * Activate the Multi-Venue SOR.
  * Allow the AI Proxy to route specific setups to Hyperliquid instead of Bybit, taking advantage of maker rebates and HLP staking.
* **Phase 4: The v4 Maker (Weeks 7+)**
  * Deploy the KASM Uniswap v4 Hook to a testnet, then mainnet with minimal liquidity.
  * Activate the Regime Oracle Pusher to allow KASM's off-chain brain to dynamically control on-chain pool fees.

### Summary of the Blueprint

By adopting this blueprint, **KASM v3.0** stops competing in the hyper-saturated, low-margin CEX retail bot space. Instead, it leverages its existing mathematical rigor (Decimals, EV scoring, PRM) to act as an **institutional-grade liquidity engineer**, capturing LVR, harvesting MEV, and dynamically shaping Uniswap v4 pools based on real-time market regimes.

This is the exact architecture required to dominate the 2026 Hybrid DeFi landscape.
