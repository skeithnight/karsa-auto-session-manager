# Quant Trader Persona Review

**Date:** 2026-07-28  
**Lens:** Quant trader reviewing the codebase for edge quality, portfolio construction, execution drag, and research discipline.  
**Scope:** Code-only review. No runtime logs, monitoring output, or paper/live results used here.

---

## Trader's Verdict

If I look at this repo like a quant PM instead of a software architect, my conclusion is:

**The system does not primarily need more indicators right now. It needs sharper edge separation, cleaner research-to-live parity, and less score inflation.**

The code already contains enough ingredients for a serious multi-regime crypto system:

- regime classification
- signal routing
- multi-timeframe confirmation
- macro filters
- portfolio risk controls
- dynamic sizing
- EV-style ranking ideas
- post-entry management

But in its current form, the stack is still too willing to:

1. mix many weak overlays into one composite score,
2. treat research scaffolding as if it were production-grade evidence,
3. mutate execution decisions after signal creation,
4. let risk and sizing logic grow faster than verified edge.

From a trading perspective, that usually leads to a system that is:

- selective, but not necessarily predictive,
- sophisticated, but hard to calibrate,
- protective, but not yet statistically sharp.

---

## What I Would Change As A Quant Trader

## 1. Reduce score stacking and force clearer edge families

### Why

The live decision path keeps adding bonus/penalty layers on top of the base strategy score:

- v3.5 filter
- symbol-performance multiplier
- ELO multiplier
- correlation penalty
- macro penalty
- carry bonus
- funding term bonus
- liquidation heatmap bonus
- cross-asset momentum bonus
- token unlock penalty
- HMM bonus/penalty
- session effects
- dip-buyer gate reduction

Much of this is concentrated in `app/consumer/decision_engine.py:530-869`.

As a quant trader, this is a red flag. Not because overlays are bad, but because **too many additive overlays make it hard to know which component actually owns the alpha**.

### Improvement

Split the strategy into **explicit edge families** instead of one giant score stack:

1. **Trend continuation**
2. **Range mean reversion**
3. **Carry / positioning dislocation**
4. **Event / momentum breakout**
5. **Liquidation / squeeze setup**

Each family should have:

- its own entry logic,
- its own filters,
- its own expected holding period,
- its own stop / target behavior,
- its own attribution in research.

### Why this matters

A trend system and a carry system should not mainly differ by “+25 points here and -10 points there.”  
They should be modeled as separate return streams.

---

## 2. Stop treating regime classification as sufficient proof of edge

### Why

The regime classifier is clean enough structurally:

- BTC 1H based
- ADX + Hurst + ATR percentile
- deterministic branching

That lives in `app/alpha/regime_classifier.py`.

But from a quant point of view, regime classification is **context**, not alpha.

A regime model helps answer:

- when trend logic is more plausible
- when mean reversion is more plausible
- when not to trade

It does **not** by itself prove that the routed entries are profitable inside that regime.

### Improvement

Track performance by:

1. regime
2. strategy family
3. direction
4. holding-time bucket

For example:

- `TREND_BULL + breakout LONG`
- `RANGE + BB fade SHORT`
- `carry LONG during negative funding`

### Why this matters

Right now the code has regime-aware logic, but the system still risks behaving like:

“We classified the market well, therefore the trade should work.”

That is not enough. A quant process needs:

“This exact setup inside this exact regime has proven expectancy.”

---

## 3. Tighten the definition of expected value

### Why

The current EV concept is directionally right, but too thin to trust fully yet.

`app/learning/expected_edge.py` currently:

- fetches recent trades for the same symbol,
- compares them using a small similarity feature set,
- averages similar-trade PnL.

`app/learning/similarity_engine.py` only compares:

- `adx_14`
- `hurst`
- `atr_pct`
- `rsi_14`

As a trader, I would treat that as a **rough context heuristic**, not a portfolio allocator.

### Improvement

Upgrade EV from “recent similar symbol memory” into “setup-level expectancy estimate.”

Minimum improvements:

1. include strategy family in similarity matching
2. include direction
3. include spread / liquidity / volatility state
4. include holding-time outcome
5. require minimum sample thresholds before EV influences ranking strongly

### Recommendation

Short term:

- keep EV as a ranking tie-breaker

Medium term:

- promote EV into allocator logic only after research confirms stability

---

## 4. Simplify Kelly and advanced sizing until the base edge is proven

### Why

The `KellySizer` in `app/risk/kelly_sizer.py` is thoughtful, but from a PM perspective it is ahead of the measurement layer.

It already includes:

- fractional Kelly
- drawdown adaptation
- uncertainty adjustment
- volatility targeting hooks

This is good engineering, but only valuable if:

1. the measured win rate is trustworthy,
2. payoff ratio is stable,
3. setup buckets are correctly segmented,
4. backtest/live parity is strong.

If not, Kelly just magnifies model error.

### Improvement

I would temporarily simplify sizing into:

1. fixed-fraction baseline by strategy family,
2. regime multiplier only after validation,
3. drawdown reduction kept,
4. Kelly as research-only shadow metric until proven stable.

### Why this matters

As a quant trader, I would rather under-size a real edge than optimally size an illusion.

---

## 5. Rework multi-timeframe and macro logic from blocker-heavy to evidence-weighted

### Why

`app/alpha/multi_tf.py` is doing sensible things:

- 4H EMA trend confirmation
- macro anchor penalty
- macro momentum hard block

But in practice, this kind of logic can easily become too blunt if it is not validated family-by-family.

For example:

- trend continuation probably benefits from strict macro alignment,
- carry dislocation may not,
- event-driven squeezes often begin while anchors still disagree.

### Improvement

Do not treat macro/MTF as one universal policy.  
Make them **family-specific filters**.

Suggested framing:

1. `trend_continuation`: strict macro alignment
2. `range_reversion`: moderate macro penalty, not hard block
3. `carry_dislocation`: macro-aware but not macro-dominated
4. `breakout_event`: allow explicit exemption path

### Why this matters

From a trading perspective, the question is not “does macro matter?”  
It is “for which setup does macro matter enough to block?”

---

## 6. Make the ML prefilter earn the right to exist

### Why

`app/alpha/ml_prefilter.py` currently:

- loads a local joblib model if present,
- otherwise fail-opens,
- predicts from a very short feature vector,
- defaults to `1.0` on failure.

This is not wrong operationally, but from a quant perspective it is still an **optional scoring ornament**, not a proven model.

### Improvement

I would not remove it, but I would reclassify it as:

- **experimental overlay**

until the repo can show:

1. calibration,
2. stability across time windows,
3. incremental value over deterministic scoring,
4. non-trivial contribution after fees and slippage.

### Recommendation

Keep the model in shadow evaluation mode longer.  
Promote it only when it clearly improves:

- precision,
- EV ranking,
- drawdown-adjusted returns.

---

## 7. Tighten portfolio logic around correlation and capital competition

### Why

The `PortfolioRiskManager` in `app/risk/portfolio_risk_manager.py` has the right idea:

- sector caps
- rolling correlation
- exposure control
- macro kill-switch
- reallocation concept

But from a quant portfolio view, the current reallocation and EV competition logic is still too rough:

- signal EV is inferred from generic confidence and tp/sl distances
- open-position EV is approximated from remaining TP/SL geometry

That is useful, but it is still more heuristic than portfolio optimizer.

### Improvement

Keep the PRM as a hard safety and crowding layer, but avoid pretending the current EV competition is fully portfolio-optimal.

Suggested next step:

1. preserve hard controls:
   - gross exposure
   - net exposure
   - correlation cap
2. delay aggressive capital reallocation until expectancy is better estimated
3. rank candidates by family-aware expected return per unit risk, not just confidence geometry

### Why this matters

A quant portfolio wants to allocate capital to the *best distinct bets*, not just the cleanest-looking isolated setup.

---

## 8. Treat execution drag as part of alpha, not a separate concern

### Why

`app/execution/sor.py` contains useful routing logic:

- post-only first
- reprice attempts
- adaptive maker behavior
- iceberg slicing
- adverse-selection aborts

That is all valuable. But as a trader, I care less about how clever routing is and more about:

- realized spread capture,
- fill probability,
- adverse selection cost,
- abandoned-opportunity cost,
- whether post-only bias is reducing realized EV.

### Improvement

Add explicit execution research around:

1. maker-only vs taker-enabled by setup family
2. average edge decay during reprice delay
3. slippage vs missed-trade tradeoff
4. profitability net of execution style

### Recommendation

Do not assume post-only is always superior just because fees are lower.  
For fast-moving continuation setups, missed fills can be more expensive than taker fees.

---

## 9. Remove research theater

### Why

As a trader, I lose trust quickly when a codebase presents research machinery that is still scaffolded.

The strongest example is `app/research/experiment_runner.py`, which still uses mock trades rather than the actual production backtest path.

That means the repo has the appearance of:

- experiment management
- validation
- promotion

without yet having the full causal connection to actual strategy behavior.

### Improvement

Research should be either:

1. real and promotable, or
2. explicitly experimental and non-binding

### Recommendation

Mark scaffold research modules clearly, or finish them.  
Do not let them sit in the middle ground.

---

## Add, Change, Or Remove?

## What to change now

1. Refactor `DecisionEngine` into smaller, family-aware components
2. Align live, shadow, and backtest candidate evaluation
3. Simplify sizing until edge measurement is stronger
4. Reclassify EV and ML overlays as provisional unless validated
5. Replace research scaffolding with real experiment runs

## What to add next

1. Per-family attribution reports
2. Regime × strategy × direction expectancy tables
3. Execution-style attribution:
   - maker
   - taker
   - missed
4. Holding-time expectancy curves
5. Symbol clustering beyond static sector labels

## What to remove or demote

1. Any overlay that cannot show incremental value in research
2. Universal hard blocks that should be family-specific
3. Overconfident capital-reallocation logic based on weak EV estimates
4. Production reliance on scaffolded research decisions

---

## Prioritized Quant Refinement Roadmap

## Priority 1 — Prove edge honestly

1. Finish real research/backtest parity
2. Make experiment runner use real strategy outputs
3. Make ablation genuinely disable components
4. Standardize promotion decisions into real machine-readable states

## Priority 2 — Separate edge families

1. trend continuation
2. range mean reversion
3. carry dislocation
4. liquidation / squeeze
5. event breakout

Each should be evaluated independently.

## Priority 3 — Simplify live scoring

1. reduce bonus stacking
2. reduce hidden mutable overlays
3. keep only modifiers with measured value

## Priority 4 — Rebuild sizing on stronger evidence

1. fixed fractional first
2. validated multipliers second
3. Kelly last

## Priority 5 — Upgrade portfolio construction

1. better correlation grouping
2. cleaner expectancy-aware ranking
3. capital competition based on proven setup returns

---

## Final Quant Opinion

If I were allocating capital to this system, I would say:

**Do not rush to add another signal. First make the existing signals compete fairly, measure them honestly, and separate them into true return streams.**

This codebase already has enough intelligence.  
What it needs now is:

- less score decoration,
- more edge attribution,
- less research theater,
- more parity,
- cleaner portfolio logic.

That is the fastest path from “complex bot” to “tradable quantitative process.”
