# KARSA Big Gainers Alpha — Implementation Plan

## Goal Description

The goal is to implement a set of features that allow KARSA to capture the massive upside of crypto "runners" (tokens gaining 30-100%+) while strictly ensuring **zero increase in initial risk**. The user explicitly requested a smooth, secure implementation to avoid large losses.

To achieve this, we will implement a hybrid approach focusing on **better entry timing** and **asymmetric upside capture**:
1. **Volume Anomaly Detector**: Catch pumps before they happen by detecting whale accumulation.
2. **Dip Buyer on Strong Performers**: Enter strong trends on their first pullback (highest win-rate setup) rather than penalizing them.
3. **Regime Conviction Scaling**: Reduce position sizes on weak, ambiguous signals to protect capital (directly addressing the "no big loss" requirement).
4. **Moon Bag (Tiered Exit)**: Instead of exiting 100% of a position on a trailing stop, exit 80% to secure profits and leave 20% with a wide stop to ride the remaining trend for massive gains.

## User Review Required

> [!CAUTION]
> **Tiered Exits (Moon Bag) modifies the Execution Layer (APM)**
> Modifying `ActivePositionManager` (APM) carries execution risk. To make this extremely safe, the 80/20 split will be handled entirely via exchange-side API orders (e.g., placing a Take Profit order for 80% at 1.5R, and letting the trailing stop manage the remaining 20%). If the exchange doesn't support multiple exit orders seamlessly, we will handle it in memory with strict fail-safes.

> [!IMPORTANT]
> **Risk Profile Validation**
> The changes strictly *decrease* risk on weak signals (Conviction Scaling) and *lock in profits earlier* (80% exit at 1.5R). Your initial stop-loss distances and initial position sizes will **not** increase.

## Open Questions

1. **Exchange Support**: Are we strictly using Bybit for all execution? (Bybit supports partial closes via multiple TP orders, but I want to confirm).
2. **Moon Bag Sizing**: Is an 80/20 split acceptable? (Lock in 80% of the position early, let 20% run to the moon).

## Proposed Changes

---

### Universe & Data Layer (Early Detection)

#### [MODIFY] `app/data/universe_scorer.py`
- **Volume Anomaly Detection**: Add logic to compare the 1H volume against the rolling 24H average. If volume spikes > 3x but price hasn't moved > 5%, add a `volume_anomaly` bonus (+20 score) to flag whale accumulation before the pump.
- **Overextension Exemption**: Modify the `overextension_penalty` so that it doesn't penalize tokens that are up >15% over 48h. These are our "strong performers". Instead, pass a flag to `DecisionEngine` indicating this is a dip-buy candidate.

---

### Alpha & Risk Layer (Protection & Conviction)

#### [MODIFY] `app/alpha/regime_classifier.py`
- **Regime Conviction Score**: When classifying a regime (e.g., `TREND_BULL`), calculate a conviction score from 0.0 to 1.0 based on how far above the threshold the metrics are (e.g., ADX 26 = 0.3 conviction, ADX 40 = 1.0 conviction).

#### [MODIFY] `app/consumer/decision_engine.py`
- **Dip Buyer Logic**: If a token has the "strong performer" flag, lower the gate threshold by 10% *only* if the current price is a -5% to -10% dip from the 48h high.
- **Conviction Sizing**: Multiply the final Kelly position size by the `regime_conviction` score. This guarantees we bet smaller on weak trends, drastically reducing losses during choppy fakeouts.

---

### Execution Layer (Letting Winners Run)

#### [MODIFY] `app/execution/position_manager.py` (ActivePositionManager)
- **Tiered Exit Management**:
  - Update the APM loop to support a `Tranche1_Closed` state.
  - When the position hits `+1.5R` (1.5x the initial risk), execute a market order to close 80% of the position.
  - Move the stop-loss for the remaining 20% (the "Moon Bag") to the original entry price (Breakeven).
  - Use an ultra-wide trailing stop (e.g., 5x ATR) for the 20% to avoid getting shaken out of massive runs.

#### [MODIFY] `app/execution/bybit_client.py`
- Ensure the `execute_exit` method can handle partial position sizes (closing a percentage of the total held amount).

## Verification Plan

### Automated Tests
- `pytest tests/alpha/test_regime_classifier.py` - Verify conviction score math.
- `pytest tests/consumer/test_decision_engine.py` - Verify position sizing correctly scales down with conviction.
- `pytest tests/execution/test_position_manager.py` - Verify the 80/20 partial close logic triggers correctly at 1.5R.

### Manual Verification
- Run the system in `SHADOW_MODE` (Shadow Executor).
- Observe the Redis logs to ensure trades trigger the 80% partial close at 1.5R and trail the remaining 20% without risking initial capital.
- Check that ADX=25 signals result in significantly smaller position sizes than ADX=40 signals.
