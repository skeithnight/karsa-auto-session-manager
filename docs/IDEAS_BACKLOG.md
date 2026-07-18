# Ideas Backlog
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed (this file is directly referenced by `MVP_SCOPE.md` §4 but was never delivered)
**Last Revised:** 2026-07-17 — WARP→WireGuard cleanup
**Purpose:** Per `MVP_SCOPE.md`'s Golden Rule — *"If it is not explicitly listed in the IN SCOPE section, we do not code it, we do not design it, and we do not discuss it until V1 is consistently profitable"* — this is where those ideas go instead of into the codebase. Every entry below is sourced from an explicit OUT OF SCOPE line in the existing docs; nothing here is invented scope creep.

---

## How to Use This File

- Adding an idea here is not approval to build it.
- Each entry needs a **re-evaluation trigger** — a concrete condition, not a vibe — before it gets promoted to `ROADMAP.md`.
- When an idea is promoted, it needs its own scoped mini-PRD (see `ROADMAP.md` Phase 8), not a silent addition to an existing sprint.

---

## Backlog

| # | Idea | Source | Why Deferred | Re-evaluation Trigger |
| :-- | :--- | :--- | :--- | :--- |
| 1 | ~~LLM in the hot execution path (real-time entry/exit decisions, "the 9 Router")~~ | `PRD.md` §7/§8, `MVP_SCOPE.md` §4 | **Permanently out of scope.** AI is mandatory in two safe positions (pre-entry analyst, post-entry judge) but strictly forbidden in SOR/risk gate. This is not deferred — it's a design constraint. | N/A — architecture decision, not backlog |
| 2 | ~~LLM background use — daily parameter tuning, regime detection, post-trade journaling~~ | `PRD.md` §7 | **Partially implemented.** Regime detection uses Hurst+ADX (deterministic). Post-trade memory injection via `trade_memory.py` is implemented. AI CryptoAnalyst and PositionJudge are mandatory. Daily parameter tuning still deferred. | Daily parameter tuning: once V1.1 has 30+ days of trade history |
| 3 | Multi-exchange execution / cross-exchange arbitrage | `MVP_SCOPE.md` §4, `PRD.md` §8 | Reintroduces the exact multi-venue proxy/state-divergence problem the single-process architecture was built to avoid | Only after Bybit-only V1.1 has run stable for several months and the team explicitly wants to reopen the architecture debate |
| 4 | Reinforcement Learning / FinRL execution agents | `MVP_SCOPE.md` §4 | Nondeterministic execution logic is unacceptable given current safety maturity (no proven track record of the deterministic SOR yet) | Once V1.1 has enough logged `trades`/`signals` history (per `DATA_MODEL.md`) to train and *offline-validate* a policy before ever letting it near live execution |
| 5 | Microservice split (separate Orchestrator/Bot containers via Redis pub/sub) | `ARCHITECTURE.md` §3, `MVP_SCOPE.md` §4 | Explicitly called a "fatal flaw" — compounds proxy latency and reintroduces two-phase-commit style state-sync risk | Only if the VPN tunnel requirement disappears entirely (e.g., Bybit lifts the geo-restriction, or a direct non-proxied path becomes available) |
| 6 | Portfolio correlation math (rolling correlation matrices across open positions) | `MVP_SCOPE.md` §4 | MVP treats every trade independently for simplicity; correlation math adds complexity with low payoff while position count is small | Once the account regularly holds more than 1–2 concurrent positions across the Top-5/Top-20 universe |
| 7 | Grafana dashboards | `MVP_SCOPE.md` §4 (Prometheus is in scope, Grafana explicitly is not) | Distraction from getting the core loop stable; raw Prometheus metrics/logs are sufficient for a single operator | Once more than one person needs to monitor the bot, or the operator wants historical trend visualization beyond raw scrape data |
| 8 | Low-cap altcoin / spot market trading | `MVP_SCOPE.md` §4, `PRD.md` §4 | Global State accuracy depends on deep liquidity across *all* read exchanges — low-cap assets produce noisy Skew/VWAP that isn't trustworthy | If a dedicated low-liquidity Alpha model with its own liquidity floor and wider bad-tick tolerance is designed and separately tested |
| 9 | ~~Redis caching layer for `GlobalStateCache`/heartbeats~~ | `DATA_MODEL.md` §2 | **Implemented.** Redis is in scope with 7+ keys (`global:state:{symbol}`, `system:heartbeat`, `system:circuit_breaker`, `system:config:regime`, `system:universe:symbols`, `karsa:memory:*`, `karsa:sector:*`, `karsa:position:*`, `ai:cache:*`). Context Issue #1 resolved. | N/A — done |
| 10 | ~~Coinbase as a 4th read exchange~~ | `PRD.md` §3 only, absent everywhere else | **Removed.** Coinbase reference removed from `PRD.md`. `CONTEXT.md` Issue #3 resolved. All docs now consistently reference Binance/OKX/Bybit only. | N/A — resolved |
| 11 | Cloud/Kubernetes production deployment target | `ARCHITECTURE.md` §7 ("Docker & Docker Compose... future cloud deployment") | Named as an eventual intent but never scoped — no target cloud provider, no K8s manifests, no CI/CD deploy pipeline defined anywhere | When V1.1 live-capital deployment planning begins (see `ROADMAP.md` Phase 6) |
| 12 | Remaining 6 layers of the "9-Layer Risk Gate" | `PRD.md` §6 item 5; `MVP_SCOPE.md` §3.E explicitly notes the MVP's 3 gates are "Stripped down from 9 layers" | The other 6 layers are **never enumerated anywhere** in the current doc set — this isn't deferred so much as never specified | Needs its own `RISK_GATE_FULL_SPEC.md` (or an extension to `RISK_AND_RUNBOOK.md`) before V1.1 — flagged as a documentation gap, not just a backlog idea |

---

## Explicitly Not on This List

Per the Golden Rule, things not mentioned anywhere in the current doc set (regardless of how interesting) don't belong here either — adding speculative ideas beyond what the docs already gesture at defeats the purpose of this file. If a new idea comes up in conversation, it should be added with a real source reference, not invented wholesale.