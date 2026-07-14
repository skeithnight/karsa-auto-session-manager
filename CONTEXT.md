# Project Context
**Project Name:** `karsa-auto-session-manager`
**Purpose of this doc:** A single place to get oriented — what this is, why it's built this way, what's still unresolved, and where to look for detail. Read this before `ARCHITECTURE.md` or `PRD.md` if you're new (human or AI).

---

## 1. TL;DR

An autonomous crypto perpetuals trading bot that reads market data from multiple exchanges (Binance, OKX, Bybit) to build a "true" global price picture, but only ever *trades* on Bybit — because Bybit requires a proxy (geo-restriction) and multi-venue execution would compound that latency into a fatal flaw. To make the proxy latency irrelevant, the strategy trades 15m–4h swing/intraday structure instead of HFT. Everything runs as a single Python `asyncio` process (not microservices) specifically to avoid internal state-sync bugs on top of an already-fragile external proxy dependency.

Currently: **design phase.** All seven core docs are marked "Approved/Locked" except `ROADMAP.md`, which is empty, and `PRD.md`, which is marked "Draft/Approved."

---

## 2. Core Thesis: "Read Global, Execute Local"

- **Read Pipeline:** CCXT Pro WebSockets ingest L2 books, trades, funding from multiple exchanges → normalized into a `GlobalState`.
- **Write Pipeline:** Trades are placed *only* on Bybit, using `GlobalState` as a leading indicator — e.g., if global sentiment turns bullish before Bybit's local price reflects it, go long on Bybit anticipating convergence.
- **Why not HFT:** The WARP proxy adds 100–300ms. Millisecond-level scalping through that latency is a guaranteed loser. The team deliberately moved up the timeframe (15m–4h) so that latency becomes noise relative to the trade's holding period.

---

## 3. The 7 Keys (Architecture's Component Map)

| # | Component | Responsibility | Code Location |
| :-- | :--- | :--- | :--- |
| 1 | Global Data Engine | CCXT Pro WS ingestion, normalization, bad-tick filtering | `app/data/` |
| 2 | Alpha Bridge | VWAP/Skew/Lead-Lag calculation, signal generation | `app/alpha/` |
| 3 | 3-Layer Risk Gate | Liquidity, spread health, circuit breaker checks | `app/risk/` |
| 4 | Bybit Executor | SOR (Post-Only → Reprice → Market), private WS via WARP | `app/execution/` |
| 5 | State Manager | Postgres sync, startup reconciliation | `app/core/state.py` |
| 6 | Watchdog & Telemetry | Heartbeats, latency tracking, dead man's switch, Prometheus | `app/watchdog/` |

Note: `app/core/session.py` (Session Orchestrator, UTC time-block regime logic) exists in the folder structure but is **not** one of these 6 keys in `ARCHITECTURE.md` — see Open Issue #5 below.

---

## 4. Key Architectural Decisions

| Decision | Rationale | Rejected Alternative |
| :--- | :--- | :--- |
| Single-process monolith | Proxy already adds ~150ms; internal IPC/Redis pub-sub between services would compound it and risks state divergence on partial failure | Microservices (Orchestrator + Bot split via Redis pub/sub) |
| Swing/Intraday (15m–4h), not HFT | Proxy latency is mathematically irrelevant at this timeframe; HFT through a proxy guarantees negative alpha | Millisecond lead-lag scalping |
| Bybit-only execution | Avoids cross-exchange arbitrage complexity and multi-venue proxy/auth overhead in V1 | Multi-exchange execution/arbitrage |
| `Decimal` everywhere for money | Float precision loss is unacceptable for PnL-bearing calculations | `float` |
| Mandatory exchange-side Stop-Loss on every fill | Bot's in-memory SL is worthless if the process or proxy dies; exchange-side SL survives a crash | Relying on bot-managed SL only |
| "Trust nothing" startup reconciliation | Postgres and Bybit can diverge after any crash; reconciliation is the only way to safely resume | Trusting last known DB state on restart |
| LLM strictly out of the hot path | LLM inference latency stacked on proxy latency would kill any speed-sensitive logic; also determinism matters for risk-critical decisions | LLM-assisted real-time entry/exit ("9 Router" in hot path) |

---

## 5. Glossary

| Term | Meaning |
| :--- | :--- |
| **WARP** | Cloudflare WARP SOCKS5 proxy, mandatory for Bybit access due to geo-restriction |
| **SOR** | Smart Order Routing — the 3-step Post-Only Limit → Reprice → Market/IOC execution logic |
| **GlobalState** | Normalized, aggregated market snapshot across read exchanges (VWAP, skew, funding, per-exchange prices/volumes) |
| **Skew** | Aggregate bid vs. ask order book volume ratio across read exchanges |
| **Lead-Lag** | Comparing a "leader" exchange's (usually Binance) price movement against Bybit's to infer directional pull |
| **Bad Tick** | A price spike >5% in <1s, treated as an exchange glitch and filtered out |
| **Dead Man's Switch** | External health ping (e.g., Healthchecks.io) — if it stops, an outside system alerts a human that the bot has frozen |
| **Reconciliation** | Startup process that treats Bybit's actual position/order state as ground truth over the local DB |
| **STALE** | Status flag for an exchange feed with no update in >15s; excluded from Alpha calculations |
| **Circuit Breaker** | Deterministic hard-stop rule (drawdown, latency, margin, stale data) that halts trading without human input |

---

## 6. Document Map

| Doc | Purpose | Status |
| :--- | :--- | :--- |
| `PRD.md` | Product vision, full V1.0/V1.1 target state, "6 Keys" as originally conceived | Draft/Approved |
| `ARCHITECTURE.md` | System design, component breakdown, tech stack, folder structure | Approved/Locked |
| `DATA_MODEL.md` | Exact schemas — Postgres DDL, Redis keys, Pydantic models | Approved/Locked |
| `MVP_SCOPE.md` | What's actually being built first, phased delivery plan, explicit out-of-scope list | Approved/Locked |
| `DEFINITION_OF_DONE.md` | Quality gates every PR must pass | Approved/Locked |
| `RISK_AND_RUNBOOK.md` | Kill switch, circuit breakers, failover, disaster recovery, operator playbook | Approved/Locked |
| `ROADMAP.md` | *(empty — see Open Issue #4)* | Missing |
| `TELEGRAM_INTERFACE.md` | Telegram bot command specs, alert system, security model | Draft |
| `TESTING_STRATEGY.md` | How each safety/behavior claim gets verified | Draft (this delivery) |
| `CLAUDE.md` | AI-agent working rules for this repo | Draft (this delivery) |

---

## 7. Open Issues & Doc Conflicts (Needs Resolution)

These are genuine contradictions found across the "Approved/Locked" docs during review. None are stylistic — each one changes what gets built or what a test should assert. Flag, don't silently pick a side.

### Issue #1 — Redis: in scope or not? → RESOLVED: In scope (code reality)
`DATA_MODEL.md` §2 defines a full Redis schema. `ARCHITECTURE.md` and `MVP_SCOPE.md` omitted Redis. **Verified:** Redis is already implemented with 7+ keys (`global:state:{symbol}`, `system:heartbeat`, `system:circuit_breaker`, `system:config:regime`, `trade:{trade_id}`, `karsa:auto:config`, `karsa:auto:state:active`, `karsa:auto:start_time`). Redis is de facto in scope. `ARCHITECTURE.md` §7 and `MVP_SCOPE.md` §3.A need updating to reflect this.
**Impact:** Phase 4 of execution plan adds `position_store.py` (Redis-backed) — consistent with existing code. Docs need to catch up.
**Status:** Docs update pending. No code change needed.

### Issue #2 — Circuit breaker drawdown threshold: 2% or 3%? → CONFIRMED CONFLICT
`circuit_breaker.py:18` defaults to **-2%** (`Decimal("-0.02")`). `RISK_AND_RUNBOOK.md` §2 specifies **-3%**. `main.py:216` instantiates `CircuitBreaker()` with no args — uses 2% code default. No config override exists.
**Additionally:** `gates.py:18` uses `float` (`-0.02`) for `daily_drawdown_limit`, violating the "No float for money" rule.
**Impact:** Safety-critical. Must be resolved before touching circuit breaker code. Code currently halts at 2%, runbook says 3%.
**Recommendation:** User picks one. Code and docs updated to match.
**Status:** BLOCKING — awaiting user decision.

### Issue #3 — Read-exchange universe inconsistency
`PRD.md` §3 lists Binance, OKX, Bybit, **and Coinbase** as read sources. Every other doc (`ARCHITECTURE.md` diagram/stack, `MVP_SCOPE.md` §3.B, folder structure) only ever mentions Binance/OKX/Bybit. Coinbase appears exactly once, nowhere else.
**Impact:** Likely a stale PRD mention rather than a real requirement, but worth a one-line confirmation so nobody builds a Coinbase adapter that isn't needed, or worse, skips one that is.

### Issue #4 — `ROADMAP.md` is empty
Uploaded but contains no content. Given `MVP_SCOPE.md` only covers V1.0/paper-trading, there's currently no documented plan for V1.1 (live capital) or beyond. Worth filling in before Phase 4 wraps, so "graduating to live capital" has a defined next step instead of ending in a vacuum.

### Issue #5 — "6 Keys" defined differently in two docs
`PRD.md` §6 lists the 6 Keys as: Global Read Engine, **Session Orchestrator**, Alpha Bridge, Local Execution, **9-Layer Risk Gate**, Telemetry & Reconciliation. `ARCHITECTURE.md` §2/§4 lists them as: Data Engine, Alpha Bridge, Risk Gate, Executor, **State Manager**, **Watchdog** — Session Orchestrator is demoted to a sub-module (`app/core/session.py`) and State Manager/Watchdog are split into two keys instead of one ("Telemetry & Reconciliation").
**Impact:** Low — mostly naming/documentation drift, since `MVP_SCOPE.md` and `DEFINITION_OF_DONE.md` both follow the `ARCHITECTURE.md` version. Worth a pass to make `PRD.md` consistent so it doesn't read as a different system.

### Issue #6 — Symbol count: 5 (MVP) vs 35 (config)
`MVP_SCOPE.md` §3.B specifies Top 5 (BTC, ETH, SOL, BNB, XRP). `app/core/config.py:35` defaults to 35 USDT pairs in 3 tiers. Config is more aggressive than MVP scope.
**Impact:** Regime detection (Phase 1) is BTC-only by definition. Other symbols inherit BTC regime. No conflict for regime, but 35 symbols means more REST calls for OI/funding.
**Status:** Needs decision — use MVP Top 5 or config's 35?

### Issue #7 — `daily_drawdown_limit` is `float`, not `Decimal`
`app/risk/gates.py:18`: `daily_drawdown_limit = -0.02` (float). CLAUDE.md Rule 1: "No float for money." All financial calculations must use `decimal.Decimal`.
**Impact:** Violates non-negotiable rule. Must be fixed.
**Status:** Scheduled for Phase 0A of execution plan.

---

## 8. Current Status

- **Phase:** Architecture-code parity achieved. All ARCHITECTURE.md features now wired in code.
- **Verified findings:** Full codebase audit completed. See `docs/review/verified_findings.md` for details.
- **Test suite:** 194 tests passing across all modules.
- **AI Layer:** Pre-entry analyst (Redis cache) + position judge wired into CheckpointManager. Off hot-path, graceful degradation when AI unavailable.
- **Watchdog:** Per-exchange heartbeat tracking, execution latency tracker with SOR switching, event loop lag with position flatten, Dead Man's Switch wired as task. All Prometheus metrics wired.
- **Next step:** Resolve blocking issues (B1: drawdown threshold, B2: symbol count decision). Then production hardening.
- **Blocking items:** Issue #2 (drawdown 2% vs 3%), Issue #6 (symbol count decision).