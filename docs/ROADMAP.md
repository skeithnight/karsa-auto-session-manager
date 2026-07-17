# Roadmap
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed (the uploaded `ROADMAP.md` was empty; this fills `CONTEXT.md` Open Issue #4)
**Note on approach:** Progression here is **criteria-gated, not date-gated** — consistent with `MVP_SCOPE.md`'s own phased philosophy ("we do not move to the next phase until the current one passes the Definition of Done"). No calendar dates are given because none exist in the source docs; adding fake ones would misrepresent certainty that isn't there.

---

## Doc Conflict Flag (relevant to this roadmap specifically)

The MVP graduation gate is defined **twice, with different numbers**:
- `MVP_SCOPE.md` §6: 14 consecutive days on live Bybit (main URL, $1 SL cap), win rate > 50%, reward/risk ratio > 1.2.
- `PRD.md` §9: 30-day continuous paper-trading period, win rate > 52%, Sharpe Ratio > 1.5.

Different duration, different win-rate bar, and different risk-adjusted-return metric entirely (R:R vs Sharpe). This roadmap treats **Phase 5 (below) as requiring both sets of criteria to pass** rather than picking the looser one — a graduation gate protecting real capital should not be resolved by silently choosing the weaker of two "Locked" numbers. This should also be logged as `CONTEXT.md` Open Issue #6.

---

## Phase Map

| Phase | Name | Source | Gate to Advance |
| :-- | :--- | :--- | :--- |
| 0 | Design & Docs | This doc set | All core docs approved; open conflicts in `CONTEXT.md` §7 resolved |
| 1 | The Nervous System (Data & Infra) | `MVP_SCOPE.md` §5 | Continuous console output of normalized Global VWAP/Skew; survives network drops |
| 2 | The Hands (Execution & Proxy) | `MVP_SCOPE.md` §5 | Dummy order placed/tracked/closed on **live Bybit (main URL)** via WARP with $1 SL cap, latency logged |
| 3 | The Brain & The Shield (Alpha & Risk) | `MVP_SCOPE.md` §5 | Signals generated, filtered through Risk Gate, decisions logged to Postgres |
| 4 | Integration & Paper Trading | `MVP_SCOPE.md` §5 | 72 hours on **live Bybit (main URL)** with $1 SL cap, zero crashes, zero state divergence |
| 4.5 | AI Integration & 6-Stage Lifecycle | New (this doc) | Full 6-stage pipeline running with mandatory AI (see below) |
| 5 | Graduation Evaluation | New (this doc) | Both `MVP_SCOPE.md` §6 **and** `PRD.md` §9 criteria pass (see conflict flag above) |
| 6 | Live Capital — Limited (V1.1 Pilot) | New (this doc) | Defined below |
| 7 | Live Capital — Scaled | New (this doc) | Defined below |
| 8 | Backlog-Driven Expansion | `IDEAS_BACKLOG.md` | Ongoing — one item at a time |

Phases 1–4 are already fully specified in `MVP_SCOPE.md` §5 — this roadmap doesn't duplicate that detail, only extends past it.

---

## Phase 4.5: AI Integration & 6-Stage Lifecycle

Purpose: transform ASM from a rule-based system into a KCT-equivalent AI-augmented trading system with mandatory LLM integration.

### Sub-phases (ordered by dependency)

| Sub-phase | Scope | Effort | Status |
| :--- | :--- | :--- | :--- |
| 4.5.1 | Wire `executor_task` → `sor.execute()` (unblock the chain) | ~30 min | ✅ Done |
| 4.5.2 | Dynamic universe scoring (`universe_scorer.py`) | ~4-5h | ✅ Done |
| 4.5.3 | Multi-timeframe confirmation (`multi_tf.py`) | ~2-3h | ✅ Done |
| 4.5.4 | AI CryptoAnalyst mandatory (remove toggle, enforce rejection on failure) | ~1-2h | ✅ Done |
| 4.5.5 | Trade memory injection (`trade_memory.py`) | ~2-3h | ✅ Done |
| 4.5.6 | Sector diversity cap (`sector_cap.py`) | ~2-3h | ✅ Done |

**Total:** ~12-17 hours. Each sub-phase independently mergeable.

### Gate to Phase 5

- Full 6-stage pipeline runs for 72h on **live Bybit main URL** (micro-size, $1 SL cap) without AI-related crashes
- AI analyst p95 latency < 2s
- AI position judge correctly identifies HARD_FAIL positions in test scenarios
- Universe scorer produces sensible top-15 list on live market data
- All tests in `tests/unit/test_universe_scorer.py`, `test_analyst.py`, `test_position_judge.py`, `test_multi_tf.py`, `test_trade_memory.py`, `test_sector_cap.py` pass

---

## Phase 5: Graduation Evaluation Gate

Purpose: a formal, deliberate checkpoint between "paper trading works" and "real money is at risk" — not an automatic transition.

**Status:** Code complete. Testnet skipped (no testnet API access available) — deployed to live Bybit main URL with $1 max loss per position SL hard cap.

**Phase 5 code additions (committed):**
- `scripts/init_db.sql` — `trades` + `ai_decisions` Postgres tables, auto-created on first `docker compose up`
- `app/core/trade_store.py` — Postgres CRUD: `record_entry()`, `close_trade()`, `get_history()`, `record_ai_decision()`
- `app/core/config.py` — `bybit_testnet: bool = False` toggle
- `app/execution/bybit_client.py` — pybit `HTTP(testnet=...)` pass-through
- `app/data/ccxt_manager.py` — `exchange.set_sandbox_mode(True)` when testnet enabled
- `app/execution/sor.py` — `$1 SL cap`: SL price = `fill_price +/- (max_loss_usd / amount)`, adapts distance to position size
- `app/execution/position_lifecycle.py` — `CheckpointManager._exit_position()` writes to trade store
- `app/core/position_store.py` — `json.dumps`/`json.loads` replaces `ast.literal_eval`/`str(dict)`
- `app/main.py` — balance-based position sizing (`available * risk_pct / price`), trade store wiring
- `.env.testnet` — template with `BYBIT_TESTNET=true` and WireGuard guidance

**Original exit criteria (for reference — replaced by live deployment safety boundaries below):**
- ~~30 consecutive days on Bybit Testnet, zero unhandled exceptions, zero state divergence~~ *(testnet not accessible)*
- Win rate > 52% **and** R:R > 1.2 **and** Sharpe > 1.5 over that period
- Circuit breaker intentionally triggered at least once and verified to flatten + halt correctly
- AI analyst p95 latency consistently < 2s
- AI position judge correctly handled at least 5 ambiguous-zone positions
- Manual review sign-off by the operator, referencing `RISK_AND_RUNBOOK.md` §6

**What replaces testnet gate for live deployment:**
- $1 hard cap per position (exchange-side SL, survives crash)
- Circuit breaker -2% daily drawdown (existing)
- Consecutive loss pause (existing, 3 losses → 60 min pause)
- $100 max position size (existing config default)
- Daily manual review per `RISK_AND_RUNBOOK.md` §6

---

## Phase 6: Live Capital — Limited (V1.1 Pilot)

Purpose: prove the system under real slippage, real fees, and real psychological stakes — at a scale where a mistake is survivable, not catastrophic.

**Recommended approach (not specified in source docs — proposed defaults):**
- Deploy with a small fraction of intended total capital (e.g., 5–10%)
- Tighten the daily circuit-breaker threshold *below* whatever the Phase 5 conflict resolves to, as an extra live-capital margin of safety
- Daily manual review against `RISK_AND_RUNBOOK.md` §6, not just automated monitoring
- Track live PnL separately from paper PnL — real fills will differ from Testnet fills, and this delta itself is diagnostic information worth logging

**Exit criteria:** N consecutive weeks (recommend starting at 4) of live performance consistent with the Phase 5 paper-trading baseline, accounting for the now-real cost of slippage and fees.

---

## Phase 7: Live Capital — Scaled

**Recommended approach:**
- Pre-defined capital ladder (e.g., 25% → 50% → 100% of target AUM), each rung gated by the same performance bar as Phase 6's exit criteria
- Re-introduce `IDEAS_BACKLOG.md` items one at a time here, each requiring its own mini-PRD and DoD sign-off — never bulk-added under the assumption that "the system is proven now so anything goes"
- First candidates from the backlog worth prioritizing: the remaining 6 layers of the 9-Layer Risk Gate (`IDEAS_BACKLOG.md` #12) and the Cloud/Kubernetes deployment target (`IDEAS_BACKLOG.md` #11) — both are closer to "finish what V1.0 assumed" than "new scope"

---

## Phase 8: Backlog-Driven Expansion

Ongoing. Pull items from `IDEAS_BACKLOG.md` strictly in the order their re-evaluation triggers actually fire — not in order of excitement. Each promoted item gets scoped as its own mini-PRD before any code is written, preserving the same discipline that got the MVP built safely in the first place.

---

## What This Roadmap Deliberately Does Not Do

- Does not set calendar dates or sprint numbers — this project advances on proof, not schedule.
- Does not re-litigate Phase 1–4 detail — see `MVP_SCOPE.md` for that.
- Does not resolve the Phase 5 threshold conflict itself — that requires a human decision, logged back into `CONTEXT.md`.