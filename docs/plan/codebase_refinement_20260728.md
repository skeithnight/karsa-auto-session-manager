# Codebase Refinement Plan

**Date:** 2026-07-28  
**Scope:** Code-only review of the repository. This note intentionally excludes runtime captures, monitoring output, and live-trading anecdotes.  
**Purpose:** Identify the highest-leverage codebase changes to improve trustworthiness, profitability research, and operational clarity.

---

## Executive View

The repo does not look blocked by a single missing strategy idea. It looks blocked by **misalignment between the live path, the research path, and the execution controls**.

The main pattern across the codebase is:

1. The live decision path has accumulated many scoring overlays, penalties, boosts, and Redis-driven modifiers inside one large function.
2. The research layer still contains scaffolding and mock behavior in places where promotion decisions are supposed to be data-driven.
3. Execution and position-management logic contain profitability-affecting overrides that no longer cleanly match the documented regime profiles.
4. Several important controls are technically present but not reliably wired end-to-end.

Because of that, the best next step is **not** “add more alpha features.”  
The best next step is to **refine the system so we can trust what is actually making or losing money**.

---

## What The Code Suggests We Should Do

### 1. Refactor the decision pipeline before adding more strategy logic

**Why**

`app/consumer/decision_engine.py` is carrying too many responsibilities at once:

- signal construction and gating live in one large method
- regime, sector, macro, carry, liquidation, HMM, unlock, ELO, volatility, symbol-performance, and EV logic are all stacked into one scoring flow
- the constructor accepts a router, but immediately replaces it with a new `StrategyRouter`, ignoring the injected dependency (`app/consumer/decision_engine.py:103-156`)

There are also correctness smells inside the scoring flow:

- `TradeSignal` is a frozen dataclass (`app/consumer/decision_engine.py:44`)
- later, live execution mutates `signal.amount` directly in `app/consumer/live_loop.py:215-244`
- sector-score logging references `score` before `score` is assigned in `app/consumer/decision_engine.py:463-469`

**Improvement**

Split `DecisionEngine.evaluate()` into explicit phases:

1. `collect_market_context`
2. `run_hard_blocks`
3. `compute_base_score`
4. `apply_score_modifiers`
5. `build_candidate_signal`
6. `rank_candidates`

**Files**

- `app/consumer/decision_engine.py`
- `app/consumer/live_loop.py`
- `app/core/decision_context.py`

**Expected payoff**

- fewer hidden interactions between modifiers
- easier backtest/live parity
- easier debugging when a profitable-looking setup gets rejected

---

### 2. Make the research layer real before trusting any promotion logic

**Why**

The research system still contains placeholders in critical places:

- `ExperimentRunner` uses mocked trades instead of the actual backtest pipeline (`app/research/experiment_runner.py:33-46`)
- the ablation framework accepts a disabled-component list but never uses it (`app/research/ablation.py:152-177`)
- the ranking gate writes decorated strings like `"✅ PROMOTE"` while the decision engine only blocks exact `"REJECT"` (`app/research/ranking_engine.py:89-94`, `app/consumer/live_loop.py:1051-1086`, `app/consumer/decision_engine.py:162-176`, `app/consumer/decision_engine.py:298-304`)

This means the repo currently has parts that *look* like rigorous research and promotion tooling, but are still not safe to treat as proof of edge.

**Improvement**

Turn research into a strict parity layer:

1. Replace experiment mocks with real backtest runs
2. Standardize ranking decisions to plain enums:
   - `PROMOTE`
   - `NEEDS_MORE_EVIDENCE`
   - `REJECT`
3. Make ablation actually toggle behavior in:
   - scoring
   - sizing
   - filtering
   - regime enhancements

**Files**

- `app/research/experiment_runner.py`
- `app/research/ablation.py`
- `app/research/ranking_engine.py`
- `app/consumer/live_loop.py`
- `app/consumer/decision_engine.py`

**Expected payoff**

- trustworthy experiment results
- promotion gates that truly affect live behavior
- less risk of optimizing around scaffolding instead of actual outcomes

---

### 3. Force live/backtest parity instead of maintaining two truths

**Why**

The code currently says the backtest engine “reuses” live components, but it still diverges in important ways:

- no AI layer, no Redis dependency (`app/backtest/engine.py:1-6`)
- a different default gate (`app/backtest/engine.py:73`)
- synthetic gate adjustments when microstructure is missing (`app/backtest/engine.py:116-177`)

Meanwhile the live path uses:

- dynamic gate thresholds from Redis (`app/consumer/decision_engine.py:178-197`, `app/consumer/decision_engine.py:833-835`)
- many score bonuses and penalties not naturally represented in the backtest flow
- live-side trade sizing adjustments after signal construction (`app/consumer/live_loop.py:182-244`)

**Improvement**

Build a single reusable “candidate evaluation” module shared by:

- live loop
- shadow loop
- backtest engine
- experiment runner

Differences should be isolated to data source, not logic.

**Files**

- `app/consumer/decision_engine.py`
- `app/backtest/engine.py`
- `app/research/experiment_runner.py`
- `app/consumer/shadow_loop.py`

**Expected payoff**

- backtest results that mean something
- fewer “it looked good offline but trades differently live” failures

---

### 4. Reconcile profitability exits with documented regime design

**Why**

The execution code has become more aggressive than the documented regime model:

- `DynamicRiskGate` still defines long regime hold windows such as TREND `2880` minutes and RANGE `240` minutes (`app/risk/dynamic_risk_gate.py:33-36`, `app/risk/dynamic_risk_gate.py:69-130`)
- `ActivePositionManager` now force-closes losing positions after `3` minutes and breakeven positions after `15` minutes (`app/execution/position_manager.py:1513-1533`)

That may be a good emergency triage rule, but it is no longer a small detail. It materially changes strategy behavior and can suppress swing expectancy before trailing or regime-based exits get a chance to work.

**Improvement**

Decide which model is intended:

- **Model A:** swing/intraday trend system with long hold windows
- **Model B:** ultra-fast thesis-validation system that kills trades quickly

Then align:

1. `DynamicRiskGate`
2. `ActivePositionManager`
3. backtest exits
4. docs

Do not keep both models half-active at once.

**Files**

- `app/execution/position_manager.py`
- `app/risk/dynamic_risk_gate.py`
- `app/backtest/engine.py`
- `docs/execution/active_position_manager.md`

**Expected payoff**

- cleaner expectancy distribution
- less confusion when analyzing why trades die flat or early

---

### 5. Simplify “expected value” so it becomes auditable

**Why**

The repo is starting to route decisions by EV, but the EV foundations are still thin:

- `ExpectedEdgeCalculator` only looks at recent trades for the same symbol (`app/learning/expected_edge.py:34-45`)
- similarity is computed from only four fields: `adx_14`, `hurst`, `atr_pct`, `rsi_14` (`app/learning/similarity_engine.py`)
- recent-memory matching is Redis-based and can be sparse or noisy for real expectancy estimation

That is useful as a heuristic, but not yet strong enough to be treated as a robust portfolio allocator.

**Improvement**

Keep the EV concept, but downgrade the current implementation from “allocator truth” to “ranking hint” until it is upgraded.

Upgrade path:

1. move expectancy lookup from Redis-only memory toward persisted trade history
2. widen feature similarity beyond four indicators
3. separate symbol-specific expectancy from setup-specific expectancy
4. require minimum sample thresholds before EV materially influences ranking

**Files**

- `app/learning/expected_edge.py`
- `app/learning/similarity_engine.py`
- `app/core/trade_store.py`
- `app/research/metrics_engine.py`

**Expected payoff**

- more trustworthy candidate ranking
- reduced risk of overfitting to a few recent symbol-specific trades

---

### 6. Reduce fail-open and hidden-fallback behavior in critical controls

**Why**

Several code paths quietly degrade into permissive behavior:

- ranking gate defaults to `PROMOTE` on missing Redis or missing key (`app/consumer/decision_engine.py:168-176`)
- dynamic gate threshold silently falls back to static defaults (`app/consumer/decision_engine.py:186-197`)
- `PortfolioRiskManager` contains placeholder circuit-breaker sections even though it is a mandatory pre-trade gate (`app/risk/portfolio_risk_manager.py:121-131`)

For a safety feature, fail-safe makes sense.  
For a profitability filter or promotion gate, silent fail-open can hide whether the system is actually using the control at all.

**Improvement**

Classify every fallback into one of three categories:

1. fail-open
2. fail-closed
3. degrade-but-mark

Then add explicit observability for each degraded path.  
If a gate is bypassed because data is unavailable, the system should say so clearly.

**Files**

- `app/consumer/decision_engine.py`
- `app/risk/portfolio_risk_manager.py`
- `app/core/observability.py`
- `app/core/metrics.py`

**Expected payoff**

- clearer operator trust
- easier attribution when live results drift from expected behavior

---

### 7. Stop building around mutable runtime overlays when the signal object is meant to be immutable

**Why**

The code sends mixed design signals:

- `TradeSignal` is frozen (`app/consumer/decision_engine.py:44`)
- but later flow mutates it in place or via `object.__setattr__` (`app/consumer/decision_engine.py:919-920`, `app/consumer/live_loop.py:242`, `app/consumer/live_loop.py:352-368`)

This creates a blurry lifecycle:

- is a signal a final decision artifact?
- or is it an editable work object?

That ambiguity matters because profitability analysis depends on knowing which version of the signal was actually executed.

**Improvement**

Choose one model:

- immutable signal plus explicit derived variants, or
- mutable execution-intent object separate from the signal

Recommended approach:

1. keep `TradeSignal` immutable
2. introduce `ExecutionIntent` or `ExecutableSignal`
3. store both pre-AI and post-AI versions explicitly

**Files**

- `app/consumer/decision_engine.py`
- `app/consumer/live_loop.py`
- `app/core/execution_intent.py`

**Expected payoff**

- cleaner audit trail
- clearer comparison between raw strategy quality and post-overlay execution quality

---

### 8. Tighten the execution layer around order viability, not just routing cleverness

**Why**

`SmartOrderRouter` is rich in tactics, but some basic execution viability still appears under-enforced:

- it spends substantial logic on slicing, maker extension, and adverse-selection checks (`app/execution/sor.py:98-220`)
- but minimum viable order constraints and stop-loss correctness are not clearly guaranteed at the earliest possible point

The codebase would benefit more from **fewer invalid attempts** than from another routing tactic.

**Improvement**

Shift execution quality focus toward:

1. pre-flight validation before any exchange call
2. guaranteed stop-loss sanity checks
3. normalized order-notional checks before routing starts
4. one clear “go / no-go” execution contract

**Files**

- `app/execution/sor.py`
- `app/consumer/live_loop.py`
- `app/risk/gates.py`
- `app/risk/portfolio_risk_manager.py`

**Expected payoff**

- fewer dead-on-arrival orders
- less API churn
- less slippage and noise from orders that never should have been attempted

---

## Recommended Order Of Work

### Phase A — Trust The System

1. Fix ranking-gate enum mismatch
2. Fix frozen-signal mutation pattern
3. Fix sector-score logging bug
4. remove or clearly mark mock/scaffold research paths

### Phase B — Create One Truth

1. extract shared candidate-evaluation logic
2. align backtest/live/shadow scoring and gating
3. align exit rules across docs, risk profiles, and APM

### Phase C — Improve Profitability Research

1. make ablation actually toggle components
2. replace mock experiment runs with real runs
3. strengthen expected-value estimation
4. improve regime-by-regime attribution

### Phase D — Then Tune Strategy

Only after A-C should the team spend real effort on:

- threshold tuning
- new alpha modules
- carry / heatmap / HMM bonus calibration
- portfolio EV reallocation

---

## Bottom Line

Based on the codebase alone, the strongest recommendation is:

**Do not add more complexity first. Refine the architecture so the existing complexity becomes measurable, consistent, and trustworthy.**

The repo already contains enough moving parts to generate edge if they are aligned.  
Right now, the bigger risk is that the system is mixing:

- real logic
- scaffold logic
- fallback logic
- profitability overrides

without a single authoritative path that all modes share.

That is the highest-value refinement target.
