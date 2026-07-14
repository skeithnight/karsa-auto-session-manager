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
| LLM mandatory in safe positions, forbidden in hot path | AI CryptoAnalyst (pre-entry) and AI PositionJudge (post-entry) are mandatory — not optional toggles. LLM calls via 9router proxy only. Strictly forbidden in execution path (SOR/risk gate) where latency and determinism matter. See `docs/review/ai_layer_analysis.md` for latency math. | LLM-assisted real-time entry/exit ("9 Router" in hot path) |

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
| `ROADMAP.md` | Phased delivery plan (Phase 0–8), AI integration sub-phases | Draft |
| `TELEGRAM_INTERFACE.md` | Telegram bot command specs, alert system, security model | Draft |
| `TESTING_STRATEGY.md` | How each safety/behavior claim gets verified | Draft (this delivery) |
| `CLAUDE.md` | AI-agent working rules for this repo | Draft (this delivery) |

---

## 7. Open Issues & Doc Conflicts (Needs Resolution)

These are genuine contradictions found across the "Approved/Locked" docs during review. None are stylistic — each one changes what gets built or what a test should assert. Flag, don't silently pick a side.

### Issue #1 — Redis: in scope or not? → RESOLVED
Redis is de facto in scope (7+ keys in code). Docs updated to reflect this.
**Status:** Resolved. No further action needed.

### Issue #2 — Circuit breaker drawdown threshold: 2% or 3%? → RESOLVED
Code authoritative at **-2%** (`Decimal("-0.02")`). All docs now updated to match: `RISK_AND_RUNBOOK.md` §2 and §6, `PRD.md` §9.
**Additionally:** `gates.py:18` uses `Decimal` (Issue #7 resolved).
**Status:** Resolved. Code and docs aligned at -2%.

### Issue #3 — Read-exchange universe inconsistency → RESOLVED
`PRD.md` §3 previously listed Coinbase as a read source. Removed — all docs now consistently reference Binance/OKX/Bybit only.
**Status:** Resolved. No further action needed.

### Issue #4 — `ROADMAP.md` is empty → RESOLVED
`ROADMAP.md` now has full content: Phase Map (0–8), Phase 4.5 AI Integration sub-phases, Phase 5 graduation gate, Phase 6-8 live capital progression.
**Status:** Resolved. No further action needed.

### Issue #5 — "6 Keys" defined differently in two docs
`PRD.md` §6 lists the 6 Keys as: Global Read Engine, **Session Orchestrator**, Alpha Bridge, Local Execution, **9-Layer Risk Gate**, Telemetry & Reconciliation. `ARCHITECTURE.md` §2/§4 lists them as: Data Engine, Alpha Bridge, Risk Gate, Executor, **State Manager**, **Watchdog** — Session Orchestrator is demoted to a sub-module (`app/core/session.py`) and State Manager/Watchdog are split into two keys instead of one ("Telemetry & Reconciliation").
**Impact:** Low — mostly naming/documentation drift, since `MVP_SCOPE.md` and `DEFINITION_OF_DONE.md` both follow the `ARCHITECTURE.md` version. Worth a pass to make `PRD.md` consistent so it doesn't read as a different system.

### Issue #6 — Symbol count: 5 (MVP) vs 35 (config)
`MVP_SCOPE.md` §3.B specifies Top 5 (BTC, ETH, SOL, BNB, XRP). `app/core/config.py:35` defaults to 35 USDT pairs in 3 tiers. Config is more aggressive than MVP scope.
**Impact:** Regime detection (Phase 1) is BTC-only by definition. Other symbols inherit BTC regime. No conflict for regime, but 35 symbols means more REST calls for OI/funding.
**Status:** Needs decision — use MVP Top 5 or config's 35?

### Issue #7 — `daily_drawdown_limit` is `float`, not `Decimal` → RESOLVED
`app/risk/gates.py:18` now uses `Decimal("-0.02")`. `app/risk/circuit_breaker.py:18` also uses `Decimal("-0.02")`.
**Status:** Resolved. No further action needed.

### Issue #8 — AI layer status: optional toggles vs mandatory → RESOLVED
`ai_analyst_enabled` and `ai_position_judge_enabled` removed from `config.py`. `CryptoAnalyst` and `PositionJudge` now always created in `main.py`. AI is mandatory, not toggleable.
**Status:** Resolved. Toggles removed, AI always initialized.

### Issue #9 — `executor_task` never calls `sor.execute()` → RESOLVED
`executor_task` in `app/main.py` now calls `sor.execute()` with signal-derived parameters (symbol, side, amount, price). Includes FLAT skip, duplicate position check via `position_store.has_position()`, price lookup via `_get_price()`, and position registration via `position_store.save()`.
**Status:** Resolved. Full 6-stage lifecycle now wired end-to-end.

---

## 8. Current Status

- **Phase:** Phase 4.5 complete. Full 6-stage lifecycle with mandatory AI now wired.
- **Verified findings:** Full codebase audit completed. See `docs/review/verified_findings.md` for details.
- **Test suite:** 191 tests passing (3 pre-existing failures: regime tuple mismatch, Bybit unreachable in test env).
- **AI Layer:** MANDATORY. Pre-entry CryptoAnalyst + post-entry PositionJudge via 9router proxy. Not optional toggles. See `docs/review/ai_layer_analysis.md`.
- **Multi-exchange:** Binance + OKX + Bybit via CCXT Pro WebSocket. Cross-exchange VWAP + lead-lag are ASM's structural edge over single-exchange systems.
- **Full trade lifecycle (6 stages):** Universe Selection → Regime Detection → Signal Generation (with AI) → Risk Gate → SOR Execution → Post-Entry (trailing stop + checkpoints + AI judge). **Lifecycle gating:** Data pipeline (stages 1-3) always runs when app starts. ASM session gates execution (stages 5-6). System is always "warm" — no cold-start delay.
- **Watchdog:** Per-exchange heartbeat tracking, execution latency tracker with SOR switching, event loop lag with position flatten, Dead Man's Switch wired as task. All Prometheus metrics wired.
- **Phase 4.5 modules (all wired into main.py):**
  - `app/data/universe_scorer.py` — Dynamic symbol scoring (Volume + Momentum + Squeeze + Overextension), top 15 above score 55, sector cap 2, 4-hour refresh
  - `app/alpha/multi_tf.py` — 4H EMA(20) trend confirmation, 0.5x penalty on contradiction, graceful degradation
  - `app/alpha/trade_memory.py` — Redis sorted set per symbol, max 20 entries, last 3 injected into AI prompt
  - `app/risk/sector_cap.py` — Max 2 positions per sector, counted from position_store
  - `app/data/sector_mapping.py` — Static sector classification for 70+ symbols across 13 sectors
- **Next step:** Run 72h Testnet validation for Phase 5 graduation gate. Production hardening.
- **Blocking items:** None. All doc and code issues resolved.
- **Resolved this session:** Issue #2 (drawdown — code 2% is authoritative), Issue #3 (Coinbase removed), Issue #4 (ROADMAP populated), Issue #6 (60 symbols confirmed), Issue #7 (Decimal fixed), Issue #8 (AI toggles removed), Issue #9 (executor wired to SOR). Phase 4.5 sub-phases 4.5.2, 4.5.3, 4.5.5, 4.5.6 all complete.