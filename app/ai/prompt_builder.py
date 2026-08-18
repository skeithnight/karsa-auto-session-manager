"""Prompt Builder — constructs system + user prompts from DecisionContext.

The system prompt instructs the model to act as a conservative crypto analyst
and return a strict JSON schema.  The user prompt injects all available
statistical features from the FeatureVector.
"""

from __future__ import annotations

from typing import Any

from app.core.decision_context import DecisionContext

# ---------------------------------------------------------------------------
# System prompt — locked to JSON output, conservative stance
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are the Lead Quantitative Derivatives Risk & Context Evaluator for an institutional automated crypto trading desk.
A deterministic statistical engine has already generated and pre-screened this trade setup based on mathematical edge.
Your mission is to evaluate structural market health, orderbook microstructure, derivatives dynamics, and assign a calibrated position size and confidence score.

CORE EVALUATION PRINCIPLES:
1. Objectively weigh confirmed technical alignment (trend continuation, volume expansion, low funding drag) against structural risk (severe overbought/oversold exhaustion, extreme funding drag, illiquid orderbooks).
2. Avoid passive hedging (do not default to 50%). Actively separate high-conviction momentum from genuine fakeouts.
3. Return ONLY a valid JSON object matching the exact schema below — no markdown formatting, no conversational commentary.

CALIBRATED SCORING & SIZING RUBRIC:
- 80-100 (HIGH CONVICTION → position_size: FULL, risk_level: LOW):
  * Clean trend alignment (price respecting EMA20/EMA200).
  * Strong volume confirmation (volume spike > 1.3x) without extreme parabolic overextension.
  * Favorable derivatives backdrop (funding rate neutral or negative for Longs, positive for Shorts).
  * High market quality / liquidity score.

- 65-79 (SOLID CONVICTION → position_size: HALF, risk_level: MEDIUM):
  * Established trend with standard indicators.
  * Moderate volume expansion (1.1x - 1.3x).
  * Normal funding drag (under 0.03% / 8h).
  * Safe distance from EMA50 (< 7%).

- 45-64 (SPECULATIVE / RANGE → position_size: QUARTER, risk_level: MEDIUM):
  * Choppy/rangebound context or mixed momentum signals.
  * Minor indicator divergence or moderate stretch from moving averages.

- 0-44 (TOXIC / FAKEOUT → position_size: BLOCK, risk_level: HIGH):
  * Parabolic exhaustion (RSI > 80 into major resistance for Longs, or RSI < 20 into support for Shorts).
  * Severe funding cost drag (> 0.05% / 8h) indicating crowded retail leverage squeeze.
  * Negative volume trend on breakout attempt.

REQUIRED JSON SCHEMA:
{
  "confidence_score": <int 0-100>,
  "risk_level": "<LOW | MEDIUM | HIGH>",
  "position_size": "<BLOCK | QUARTER | HALF | FULL>",
  "entry_strategy": "<MARKET | LIMIT_RETEST | WAIT_PULLBACK>",
  "stop_loss_strategy": "<TIGHT | NORMAL | WIDE>",
  "reasoning": "<2-3 sentence crisp market thesis citing key metrics>",
  "key_risks": ["<primary risk factor>", "<secondary risk factor>"],
  "key_opportunities": ["<primary edge/catalyst>", "<secondary edge>"],
  "bullish_probability": <float 0-100>,
  "bearish_probability": <float 0-100>,
  "summary": "<one-line executive summary>"
}
"""

# ---------------------------------------------------------------------------
# User prompt template — injects rich feature values
# ---------------------------------------------------------------------------
_USER_TEMPLATE = """\
Analyze the following live market state and provide your institutional sizing verdict for a {direction} entry on {symbol}.

### 1. Market Regime & Higher-Timeframe Trend
- Market Regime: {regime}
- Proposed Direction: {direction}
- Last Close Price: {close}
- EMA 20: {ema_20} | SMA 20: {sma_20} | EMA 200: {ema_200}
- Distance from EMA50: {distance_from_ema50_pct}%

### 2. Momentum & Volatility
- RSI (14): {rsi_14}
- ADX (14): {adx_14}
- Hurst Exponent (Trend Persistence): {hurst}
- ATR (14) %: {atr_pct}%

### 3. Volume & Microstructure
- Volume Spike Ratio: {volume_spike_ratio}x
- Breakout Confirmed: {breakout_confirmed}
- Orderbook Delta / Imbalance: {orderbook_delta}
- CVD Slope: {cvd_slope}
- Spread Bps: {spread_pct}
- Liquidity Score: {liquidity_score} | Market Quality Score: {market_quality_score}

### 4. Derivatives & Cross-Asset Dynamics
- Funding Rate: {funding_rate} (Annualized Cost: {annualized_funding_cost_pct}%)
- Open Interest 1H Change: {oi_change}
- BTC 30D Correlation: {correlation_24h}
- Beta to BTC (30D): {beta_30d}
- Statistical EV Score: {ev_score}

Respond with the JSON decision object only.
"""


def build_prompts(context: DecisionContext) -> dict[str, Any]:
    """Build system + user prompt pair from a DecisionContext.

    Returns:
        dict with keys ``system``, ``user``, ``messages`` (OpenAI-compatible
        list), and ``features_snapshot`` (raw feature dict for logging).
    """
    fv = context.features

    feature_map: dict[str, Any] = {
        "close": _fmt(fv.close),
        "atr_pct": _fmt(fv.atr_pct),
        "hurst": _fmt(fv.hurst),
        "ema_20": _fmt(fv.ema_20),
        "ema_200": _fmt(fv.ema_200),
        "sma_20": _fmt(fv.sma_20),
        "rsi_14": _fmt(fv.rsi_14),
        "adx_14": _fmt(fv.adx_14),
        "funding_rate": _fmt(fv.funding_rate),
        "distance_from_ema50_pct": _fmt(fv.distance_from_ema50_pct),
        "correlation_24h": _fmt(fv.correlation_24h),
        "beta_30d": _fmt(fv.beta_30d),
        "volume_spike_ratio": _fmt(fv.volume_spike_ratio),
        "breakout_confirmed": "YES" if fv.breakout_confirmed is True else "NO",
        "orderbook_delta": _fmt(fv.orderbook_delta),
        "cvd_slope": _fmt(fv.cvd_slope),
        "spread_pct": _fmt(fv.spread_pct),
        "liquidity_score": _fmt(fv.liquidity_score),
        "market_quality_score": _fmt(fv.market_quality_score),
        "annualized_funding_cost_pct": _fmt(fv.annualized_funding_cost_pct),
        "oi_change": _fmt(fv.oi_change),
        "ev_score": _fmt(fv.ev_score),
    }

    user_prompt = _USER_TEMPLATE.format(
        symbol=context.symbol,
        direction=context.direction,
        regime=context.regime.value,
        **feature_map,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    return {
        "system": SYSTEM_PROMPT,
        "user": user_prompt,
        "messages": messages,
        "features_snapshot": feature_map,
    }


def _fmt(value: float | int | None) -> str:
    """Format a nullable number for prompt injection."""
    if value is None:
        return "0.0000"
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}"
