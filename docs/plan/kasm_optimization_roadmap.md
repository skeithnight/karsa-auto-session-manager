# KASM Optimization & Profitability Roadmap (v2)

**Project:** `karsa-auto-session-manager`
**Purpose:** Prioritized improvement plan, restructured around the core diagnosis from review: the current system is built **Protect → Protect → Protect → Protect → Hope there's an edge**, when a profitable system needs to be built **Find Edge → Prove Edge → Measure Edge → Scale Edge → Protect Edge**. Everything below is sequenced against that order, on top of a hard prerequisite (Phase 0) that the system is not silently broken.

> Intended use: feed this into Claude Code against the actual codebase to produce a concrete implementation plan, effort estimates, and file-level diffs per item.

---

## How to use this document

Work top-to-bottom. Each phase assumes the previous phase is actually done, not just started.

- **Phase 0 is non-negotiable and blocks everything.** Any profitability conclusion drawn on top of an unverified kill switch, an uninstantiated `ClosedPaperTrade`, or a connection-pool bug is not trustworthy — it doesn't matter how good the strategy is if the measurement layer is broken.
- **Phases 1–4 are "Find/Prove/Measure/Scale Edge."** This is deliberately *before* the heavy risk infrastructure gets touched again. Building more protection on top of an unproven edge is the mistake that got flagged — don't repeat it here.
- **Phase 5 ("Protect Edge") is where the existing risk stack — Kelly, GARCH, HMM, sector caps, correlation engine, VPN/infra hardening — gets right-sized.** It comes last on purpose: you can't correctly size protection for an edge you haven't measured yet.

---

## Phase 0 — Critical Fixes (do first, no exceptions)

These are known defects. Every downstream metric, backtest result, and profitability claim depends on these being fixed and *verified*, not just patched.

- [ ] **Fix `ClosedPaperTrade` never being instantiated** — shadow mode's win rate, expectancy, and any derived stat are currently wrong until this is fixed. Audit every place that reads shadow trade outcomes downstream of this bug.
- [ ] **Fix and live-fire test the kill switch** — code review is not enough. Trigger `/kill_karsa` (or the file flag) end-to-end: confirm open orders cancel, confirm positions flatten at market, confirm the global halt event actually stops new entries, confirm process exits cleanly. Test on testnet or with minimal real capital, not just unit tests.
- [ ] **Verify position reconciliation actually runs on schedule** — confirm `position_reconciler_task()` executes every 5 minutes as documented, not just that the function exists. Add a metric/log line that proves each run happened and what it found (clean / orphaned / ghost / rebuilt).
- [ ] **Fix asyncpg connection pool exhaustion** (event-loop binding bug, `overflow=-10`) — this will surface exactly when trade volume spikes during volatility, which is the worst time for state corruption. Reproduce it under load before calling it fixed.
- [ ] **Confirm TP-side partial fill sync** — audit that partial fills correctly update position size, average entry, and R-multiple calculations; a desync here silently breaks every downstream risk and exit decision.

**Exit criteria:** all five items verified with an actual test/drill, not just a code change. Log or document the verification.

---

## Phase 1 — Find Edge (Research Before More Trading Logic)

Goal: before writing any more strategy or risk code, find out which of the signals you already have actually carry predictive information. Right now `trend / breakout / BB / RSI / EMA / MACD / ADX / ATR / Hurst / funding / OI / lead-lag` are filters that describe price — none of them has been shown to predict it. This phase answers that, using data you're already logging.

- [ ] **Stand up an offline research pipeline separate from the live path** — historical candle/tick data → feature generation → experiment harness. This does not need to touch `app/` production code yet; it's pure research infrastructure.
- [ ] **Run feature analytics on every existing signal** — feature importance, mutual information, and correlation pruning across `ta_tools.py` indicators and the composite signals in `signals.py` (skew, lead_lag, funding, OI). Goal: find out which specific inputs carry real signal vs. which are redundant or noise, before optimizing anything downstream of them.
- [ ] **Start logging AI-decision-vs-outcome pairs immediately, even before the backtest engine exists** — AI responses can't be replayed retroactively, so every day without this logging is a day of calibration data lost permanently. Log `(AI confidence score, features seen, eventual trade outcome)` for every `analyst.py` and `position_judge.py` call, live and shadow.
- [ ] **Produce a ranked shortlist of candidate signals worth keeping** — output of this phase should be a concrete list: "these N signals show measurable predictive value, these M don't." This list is what Phase 2's backtest engine gets built and tested against — don't backtest everything blindly.

**Exit criteria:** a documented, data-backed answer to "which of our existing signals actually predict price movement," not an assumption.

---

## Phase 2 — Prove Edge (Backtest, EV Ranking, AI Calibration)

This is where the shortlist from Phase 1 gets tested rigorously. `app/backtest/` is currently unbuilt (0%) — this is the single highest-leverage engineering investment in the whole roadmap.

- [ ] **Build `app/backtest/engine.py`** — must replay the *actual* production decision pipeline (`regime_classifier` → `strategy_router` → `entry_filter` → risk gates) against historical candle data, not a simplified proxy. Backtest and live should never diverge in logic, only in data source.
- [ ] **Replace the reject-cascade with EV-based ranking** — the pipeline today (Universe → Regime → Strategy → Entry Filter → Multi-TF → AI → Risk → Portfolio → Sector → Execution) only ever asks "should I reject this?" Add an explicit scoring step after the hard safety gates (liquidity, spread, circuit breaker stay binary): `EV = P(win) × avg_reward − P(loss) × avg_risk`, computed per surviving candidate. Take the top-N by EV per cycle instead of the first candidate that clears every threshold — a 40%-win/5R-reward trade can beat a 90%-win/0.5R-reward trade, and today's system can't tell the difference.
- [ ] **Build walk-forward parameter optimization** (`app/backtest/optimizer.py`) — grid search or Bayesian optimization over gate thresholds (confidence cutoff, breakeven trigger, trailing multiplier, sector cap, etc.), with a strictly held-out out-of-sample test window that is never touched during optimization.
- [ ] **Add Monte Carlo resampling** — trade-sequence resampling to check whether the measured edge is statistically stable or an artifact of trade ordering / a lucky historical window.
- [ ] **Ensure backtest fill realism matches shadow mode** — shadow mode already models maker/taker fee asymmetry and funding rate drag (the 4 refinements). The backtest engine must use the same fill/slippage model, or optimization results will be systematically optimistic.
- [ ] **Build the AI calibration tracker from Phase 1's logged data** — produce a reliability chart: of all trades where AI said ~85% confidence, what fraction actually won? Compare quant-only vs. blended (quant×0.5 + ai×0.5) performance against realized outcomes. If the AI component doesn't measurably improve precision, recall, or EV over quant-alone, that's your answer on whether the mandatory AI gate is worth its cost and latency.
- [ ] **Build a standard backtest report** (`app/backtest/formatter.py`) — Sharpe/Sortino, max drawdown, win rate, expectancy per R, expectancy per regime, and trade count per bucket (flag low-sample-size results explicitly).

**Exit criteria:** you can answer "is strategy X profitable after fees and slippage, and is that edge stable or luck?" with numbers and a confidence interval — for the ranking approach, not just the old threshold approach.

---

## Phase 3 — Measure Edge (Ablation, Attribution, Re-tuning)

Now that Phase 2 gives you a working measurement tool, use it to find out which of the system's existing components actually contribute to the edge, and re-tune what's kept.

- [ ] **Ablation-test every "sophisticated" model component** — for each of HMM regime detection, GARCH volatility forecasting, XGBoost ML pre-filter, and Kelly sizing: run the backtest engine with and without that component on identical historical data. Keep only what demonstrably improves expectancy or risk-adjusted return over the simpler baseline (e.g., ADX+Hurst alone, fixed-fractional sizing).
- [ ] **Add per-regime, per-strategy-branch expectancy tracking** — need to know if TREND_BULL momentum entries are profitable independent of RANGE fade entries and CHOP-adjacent entries. Currently all outcomes are lumped into one aggregate win rate.
- [ ] **Audit gate independence, not just gate count** — log per-gate pass/reject reasons. If gates are positively correlated (e.g. liquidity and spread both firing on the same thin symbols), the funnel is less restrictive than a naive multiplicative estimate suggests — find out which gates are doing independent filtering work vs. redundant work, and cut redundant ones.
- [ ] **Re-tune exit rules against the actual R-multiple distribution** — check whether the breakeven trigger (+0.25R) and trailing activation (+1.5R) sit at the real inflection point in your R-distribution where trades become likely winners, rather than round numbers chosen a priori.
- [ ] **Validate session/volatility filters with data** — confirm the 04:00–12:00 UTC session block and the volatility floor (90-day 25th percentile BTC ATR) are net-positive filters in backtest, and recheck calibration periodically as market conditions shift.
- [ ] **Stress-test sector cap / correlation trap during real altcoin-wide moves** — most alts correlate hard to BTC beta during volatility spikes regardless of nominal "sector" classification. Backtest specifically against known high-correlation crash/pump events.

**Exit criteria:** a documented attribution — which components measurably add edge, which are dead weight, and which thresholds are now data-derived instead of guessed.

---

## Phase 4 — Scale Edge (Capital Allocation & Strategy Expansion)

Once there's a measured, attributed edge, this phase is about sizing and expanding it correctly — including matching the system's complexity to the actual capital it's deployed with.

- [ ] **Build a portfolio-level EV allocator** — rank all currently-surviving candidates by EV and allocate capital across the best opportunities in a cycle, instead of making isolated per-symbol yes/no decisions. This is the natural extension of Phase 2's EV ranking into position sizing.
- [ ] **Right-size complexity and position sizing to actual account size** — audit the current risk stack (Kelly, GARCH-adjusted sizing, correlation engine, portfolio risk manager) against the real capital being deployed. Institutional-grade risk infrastructure sized for a $500M fund adds latency and false confidence without adding value if the account can't actually express meaningfully differentiated position sizes. This doesn't mean deleting the stack — it means confirming the granularity of the sizing logic matches the granularity the account can actually act on.
- [ ] **Add the "gainer token" momentum strategy as a separate, smaller bucket** — real edge family, but adverse-selection-heavy:
  - Keep the overextension penalty (-40 to -10 on >30% 24h moves) conservative; validate any loosening through the Phase 2 backtest engine, not eyeballed charts (hindsight-selected "missed pumps" are survivorship bias).
  - Treat the liquidity floor as the primary risk lever, not momentum size — the current $1M 24h volume floor is conservative for genuine early-gainer capture (often shows up at $200k–$1M volume); if loosening it, pair with smaller position size and tighter slippage tolerance.
  - Give new-listing entries their own risk profile — separate sector cap and tighter circuit breaker, not blended into the main risk budget.
  - Only scale allocation to this sleeve after Phase 2/3 show backtest-validated positive expectancy after realistic fees, slippage, and reversal cases.

**Exit criteria:** capital allocation is EV-driven and portfolio-level, not isolated per-signal accept/reject, and system complexity is matched to what the account size can actually exploit.

---

## Phase 5 — Protect Edge (Right-Size Risk Infra & Harden Operations)

This comes last on purpose. The existing protection stack — Kelly, GARCH, HMM, sector caps, correlation engine, VPN/container architecture — was built before the edge was proven. Now that Phases 1–4 have measured what actually works, use that data to decide what protection is worth keeping, and only now invest in operational hardening that isn't blocking profitability measurement.

- [ ] **Finalize the fate of each ablated component from Phase 3** — for anything that didn't demonstrate value (HMM, GARCH, XGBoost pre-filter, or parts of Kelly sizing), either remove it or explicitly document why it's kept despite not showing measurable edge (e.g., as a tail-risk hedge rather than an edge source).
- [ ] **Resolve the AI-gate architecture decision using Phase 2's calibration data** — now that you know whether AI outperforms quant-alone:
  - If yes and calibration is decent: consider distilling AI decisions into a deterministic classifier trained on the logged outcome data, making it fully backtestable going forward.
  - If no or calibration is poor: demote it from "mandatory gate" to an optional discretionary overlay, sized down accordingly in risk.
- [ ] **Reconcile the "5-Container Fleet" claim with reality** — Phase 1 of the original roadmap (container split) is 0% complete while the trading engine is ~90%. Either commit to finishing the container split or update `ROADMAP.md`/`ARCHITECTURE.md` to stop implying fault isolation that doesn't exist yet.
- [ ] **Audit the VPN/gluetun single-point-of-failure** — every container (data, live, shadow, AI proxy, Telegram bot) shares one gluetun network namespace. Document the blast radius of a gluetun failure and decide if the kill-switch path specifically needs an independent network route. This is infra hardening, not alpha — correctly deprioritized until now, but real once trading with a proven edge at meaningful size.
- [ ] **Re-evaluate the macro narrator's sizing multipliers** (1.0x / 0.25x / 0.5x for RISK_ON/RISK_OFF/CHOP) using Phase 3 data — confirm these specific multipliers, not just the regime classification itself, add value over flat sizing.

**Exit criteria:** the risk/protection stack matches what Phases 1–4 actually proved is needed — nothing kept out of habit, nothing missing that the data says matters.

---

## Suggested Execution Order

1. **Phase 0** (all items, fully verified) — blocks everything, no exceptions.
2. **Phase 1** — feature analytics + start AI-outcome logging immediately (this data can't be recovered retroactively, so start day one).
3. **Phase 2** — backtest engine, EV ranking, AI calibration tracker. This is the core infrastructure investment.
4. **Phase 3** — ablation, attribution, and re-tuning, using Phase 2's engine.
5. **Phase 4** — EV-based capital allocation, complexity right-sizing, gainer sleeve (validated, not guessed).
6. **Phase 5** — finalize risk-stack decisions and operational/infra hardening, informed by everything above.

---

*Source context: consolidated from `CODEBASE.md` architecture review, known defect list, and a strategic review focused on "optimizing for edge vs. optimizing for safety," as of 2026-07-27. Cross-reference against current `AGENTS.md` and `CONTEXT.md` for any items already resolved since this was written.*
