# Phase 7: Documentation Synchronization — Align System Spec & Guidance

**Impact:** 🔥🔥🔥  
**Effort:** Medium (updates across root guides & core `docs/` specifications)  
**Risk:** Low (documentation & specification alignment, zero code breakage)

---

## The Problem

After implementing Phases 1 through 6, the system's operational paradigm shifts fundamentally:
- **From** a 25-filter binary kill-chain **to** a single Expected Value (EV) composite scoring pipeline.
- **From** a "don't trade in CHOP" mindset **to** active CHOP sub-strategies (carry, mean-revert, sweep scalps) and `TRANSITION` regime detection.
- **From** AI-as-a-veto-gate **to** AI-as-a-signal-ranker, exit brain, and regime disambiguator.
- **From** hardcoded 3% position sizing **to** Fractional Kelly Criterion with drawdown-adaptive adjustment.
- **From** single global constants **to** per-asset microstructure calibration profiles.

If `CLAUDE.md`, `CONTEXT.md`, `AGENTS.md`, and the core `docs/` specifications are not synchronized with this profitability architecture, future development will drift back into conservative over-filtering, stale field names, and conflicting operational assumptions.

---

## Scope & Exclusion Criteria

### Included in Phase 7 Sync:
1. **Root Operational Docs:** `CLAUDE.md`, `CONTEXT.md`, `AGENTS.md`.
2. **Core Specification Docs (`docs/` root and subdirectories):**
   - `docs/ARCHITECTURE.md` & `docs/architecture/*.md`
   - `docs/SYSTEM_CONSTANTS.md`
   - `docs/DATA_MODEL.md`
   - `docs/RISK_AND_RUNBOOK.md` & `docs/risk/*.md`
   - `docs/execution/active_position_manager.md` & `docs/E2E_WORKFLOW.md`, `docs/E2E_WORKFLOW_ANALYSIS.md`
   - `docs/DEFINITION_OF_DONE.md` & `docs/TESTING_STRATEGY.md`
   - `docs/AI_INTERFACE.md`
   - `docs/CONFIGURATION.md`, `docs/METRICS_DICTIONARY.md`, `docs/EVENTS.md`, `docs/DATA_RETENTION.md`
   - `docs/TELEGRAM_INTERFACE.md`, `docs/ROADMAP.md`, `docs/CODEBASE.md`, `docs/MVP_SCOPE.md`, `docs/PRD.md`

### Excluded from Phase 7 Sync:
- `docs/plan/*` (Maintained as historical/forward-looking planning artifacts)
- `docs/review/*` (Maintained as immutable code review and audit logs)

---

## Synchronization Requirements by Target Document

### 1. Root Guidance Documents

#### A. `CLAUDE.md`
- **Commands & Workflows:** Add test commands for `EVScorer`, `AssetCalibrator`, `AIRanker`, `EVThreshold`.
- **Architectural Rules:** Document the EV-first pipeline principle ("Safety comes from Kelly sizing & exchange-side SL, not signal vetoes").
- **AI Rules:** Clarify AI's role shift — batch ranking (no vetoes), position exit brain, and regime disambiguation via 9router proxy.

#### B. `CONTEXT.md`
- **System State & Orientation:** Update current operational state to reflect Phase 1-6 pipeline (EV scoring, 30s TTL, Kelly sizing).
- **Known Conflicts Resolution:** Resolve legacy numeric conflicts (e.g., hardcoded 3% risk vs Kelly sizing, 5s TTL vs 30s TTL, 85 CHOP gate vs dynamic EV threshold).
- **Active Strategies:** Add CHOP Carry, CHOP Mean Reversion, CHOP Sweep Scalp, and TRANSITION regime breakout entries to active strategy manifest.

#### C. `AGENTS.md`
- **Module Personas & Invariants:**
  - *Alpha Agent:* Must output EV score (0.0 to 1.0+); cannot apply hard binary vetoes outside spoofing/data staleness.
  - *Risk Agent:* Enforces Kelly sizing, portfolio correlation, and daily loss circuit breakers — must NOT block trades for minor spread or session noise.
  - *Execution Agent:* Executes signals with 30s TTL, handles adaptive reprice, respects regime-dependent price deviation limits.
- **Directory Map & File Listings:** Include `app/alpha/ev_scorer.py`, `app/alpha/ai_ranker.py`, `app/data/asset_calibrator.py`, `app/pipeline/` refactored modules.
- **Source of Truth Order:** Update references to include `docs/plan/crypto-trader/00_MASTER_PLAN.md` as authority for profitability architecture.

---

### 2. Core Architecture & Pipeline Specifications (`docs/`)

#### A. `docs/ARCHITECTURE.md` & `docs/architecture/*.md`
- Update 6-stage pipeline diagrams to reflect the **EV Composite Pipeline**:
  `Data Stream → Per-Asset Calibration → EV Composite Scoring → AI Batch Ranker → Kelly Risk Gate → SOR Execution → APM Exit Brain`.
- Document Multi-Resolution Regime classification (15m / 1H / 4H) and `TRANSITION_BULL`/`TRANSITION_BEAR` states.
- Document `EVScorer` component weighting and dynamic threshold engine.

#### B. `docs/SYSTEM_CONSTANTS.md`
- Add EV composite scoring default weights (`regime_alignment`, `momentum_strength`, `microstructure`, `funding_edge`, etc.).
- Add dynamic EV threshold bounds (`BASE_THRESHOLD = 0.55`, range `0.40 - 0.85`).
- Add per-regime `RiskProfile` updates (`TRANSITION` profiles, CHOP sub-strategy parameters).
- Add execution velocity constants (`SIGNAL_TTL_S = 30`, `MAX_SIGNAL_PER_CYCLE = 15`, regime-adaptive cooldowns).

#### C. `docs/DATA_MODEL.md`
- Add Pydantic / dataclass schemas for:
  - `EVComponents`
  - `RankedSignal`
  - `CalibrationProfile`
  - `RejectedSignal` (Redis stream schema `karsa:rejected_signals`)
- Update `MarketRegime` enum to include `TRANSITION_BULL`, `TRANSITION_BEAR`, `CHOP_CARRY`, `CHOP_MEAN_REVERT`, `CHOP_SWEEP`.
- Document Redis key schemas: `karsa:calibration:{symbol}`, `karsa:oi:delta_1h:{symbol}`, `karsa:liq:cascade:{symbol}`.

#### D. `docs/RISK_AND_RUNBOOK.md` & `docs/risk/*.md`
- Document drawdown-adaptive Kelly sizing rules (Anti-Martingale multipliers: `0.25x` severe, `0.50x` moderate, `1.25x` near-peak).
- Document session activity manager interaction with PortfolioRiskManager.
- Clarify emergency procedures when AI Ranker or AI Exit Brain encounters rate-limits or timeouts (fail-open to deterministic EV ordering).

#### E. `docs/execution/active_position_manager.md`, `docs/E2E_WORKFLOW.md` & `docs/E2E_WORKFLOW_ANALYSIS.md`
- Document APM integration with **AI Exit Brain** (`PositionJudge` in ambiguous P&L zone `+0.3R` to `+0.8R`).
- Document 30s TTL signal handling and adaptive reprice behavior.
- Update E2E workflow text and diagrams from legacy `main.py` monolithic flow to refactored `pipeline/` architecture.

#### F. `docs/DEFINITION_OF_DONE.md` & `docs/TESTING_STRATEGY.md`
- Add DoD criteria for new profitability components: EV scoring unit coverage >90%, Kelly sizer integration test, AssetCalibrator caching test.
- Add test suites for transition detection and batch AI ranking fallback.

#### G. `docs/AI_INTERFACE.md`
- Rewrite AI interface specification:
  - Section 1: Pre-entry **AI Signal Ranker** (batch JSON output, no vetoes).
  - Section 2: Post-entry **AI Exit Brain** (ambiguous zone decision: `HOLD`, `TIGHTEN_TRAIL`, `PARTIAL_EXIT`, `FULL_EXIT`).
  - Section 3: **Regime Disambiguator** (resolving low-conviction Hurst/ADX states).

#### H. Additional Operational Docs (`CONFIGURATION.md`, `METRICS_DICTIONARY.md`, `EVENTS.md`, `TELEGRAM_INTERFACE.md`, `ROADMAP.md`, `CODEBASE.md`, `MVP_SCOPE.md`, `PRD.md`)
- `METRICS_DICTIONARY.md`: Add Prometheus metrics for `ev_score`, `hypothetical_rejected_ev`, `ai_ranking_latency`, `asset_calibration_age`.
- `CONFIGURATION.md`: Add environment settings for EV thresholds, Kelly parameters, calibration TTLs.
- `EVENTS.md`: Document Redis pub/sub events for calibration updates and transition triggers.
- `CODEBASE.md` & `ROADMAP.md`: Update folder map and completed milestone tracking for Phase 1-6 profitability refactor.

---

## Action Plan & Execution Steps

```
Step 1: Update Root Core Documents
  ├── Edit CLAUDE.md
  ├── Edit CONTEXT.md
  └── Edit AGENTS.md

Step 2: Update Core Architecture & Data Specs
  ├── Edit docs/ARCHITECTURE.md & docs/architecture/*.md
  ├── Edit docs/SYSTEM_CONSTANTS.md
  └── Edit docs/DATA_MODEL.md

Step 3: Update Risk, Execution & AI Specs
  ├── Edit docs/RISK_AND_RUNBOOK.md & docs/risk/*.md
  ├── Edit docs/execution/active_position_manager.md, E2E_WORKFLOW.md, E2E_WORKFLOW_ANALYSIS.md
  └── Edit docs/AI_INTERFACE.md

Step 4: Update Verification & Utility Specs
  ├── Edit docs/DEFINITION_OF_DONE.md & TESTING_STRATEGY.md
  └── Edit docs/METRICS_DICTIONARY.md, CONFIGURATION.md, EVENTS.md, ROADMAP.md, CODEBASE.md
```

---

## Verification & DoD for Phase 7

- [ ] `CLAUDE.md`, `CONTEXT.md`, and `AGENTS.md` completely match the Phase 1-6 EV pipeline design.
- [ ] No file in `docs/` (outside `docs/plan/` and `docs/review/`) contains references to legacy 25-filter veto logic or hardcoded 3% risk.
- [ ] All new Redis keys, Pydantic models, and system constants are fully cross-referenced in `DATA_MODEL.md` and `SYSTEM_CONSTANTS.md`.
- [ ] `graphify update .` is executed after all documentation changes to maintain knowledge graph accuracy.
