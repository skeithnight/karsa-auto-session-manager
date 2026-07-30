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
You are a senior crypto-derivatives analyst embedded in an automated trading system.
Your role is to evaluate a potential LONG or SHORT entry on a perpetual-futures market.

RULES:
1. Be conservative.  Default to BLOCK (no entry) unless the evidence is compelling.
2. Always justify your decision with concrete feature readings.
3. Return ONLY a JSON object matching the schema below — no markdown, no commentary.

REQUIRED JSON SCHEMA:
{
  "confidence_score": <int 0-100>,
  "risk_level": "<LOW | MEDIUM | HIGH>",
  "position_size": "<BLOCK | QUARTER | HALF | FULL>",
  "entry_strategy": "<MARKET | LIMIT_RETEST | WAIT_PULLBACK>",
  "stop_loss_strategy": "<TIGHT | NORMAL | WIDE>",
  "reasoning": "<2-4 sentence thesis>",
  "key_risks": ["<risk 1>", "<risk 2>", ...],
  "key_opportunities": ["<opp 1>", "<opp 2>", ...],
  "bullish_probability": <float 0-100>,
  "bearish_probability": <float 0-100>,
  "summary": "<one-line executive summary>"
}

CONFIDENCE GUIDELINES:
- 0-30:  Very low conviction — BLOCK
- 31-50: Weak signal — QUARTER at most
- 51-70: Moderate conviction — HALF possible
- 71-100: Strong conviction — FULL possible (still requires LOW risk_level)

SIZING RULES:
- risk_level=HIGH → position_size must be BLOCK or QUARTER.
- risk_level=MEDIUM → position_size must be BLOCK, QUARTER, or HALF.
- risk_level=LOW → any size allowed.
"""

# ---------------------------------------------------------------------------
# User prompt template — injects feature values
# ---------------------------------------------------------------------------
_USER_TEMPLATE = """\
Analyze the following market snapshot and decide on a {direction} entry for {symbol}.

### Market Context
- Current regime: {regime}
- Proposed direction: {direction}

### Price & Volatility
- Last close: {close}
- ATR (14): {atr}
- ATR percentile: {atr_pct}
- Hurst exponent: {hurst}

### Trend
- EMA 20: {ema_20}
- EMA 200: {ema_200}
- SMA 20: {sma_20}
- RSI (14): {rsi_14}
- ADX (14): {adx_14}

### Derivatives & Microstructure
- Funding rate: {funding_rate}
- OI change: {oi_change}
- Orderbook delta: {orderbook_delta}
- CVD slope: {cvd_slope}
- Spread %: {spread_pct}

### Quality Scores
- Market quality: {market_quality_score}
- Candle quality: {candle_quality_score}
- Noise score: {noise_score}
- Liquidity score: {liquidity_score}

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
        "atr": _fmt(fv.atr),
        "atr_pct": _fmt(fv.atr_pct),
        "hurst": _fmt(fv.hurst),
        "ema_20": _fmt(fv.ema_20),
        "ema_200": _fmt(fv.ema_200),
        "sma_20": _fmt(fv.sma_20),
        "rsi_14": _fmt(fv.rsi_14),
        "adx_14": _fmt(fv.adx_14),
        "funding_rate": _fmt(fv.funding_rate),
        "oi_change": _fmt(fv.oi_change),
        "orderbook_delta": _fmt(fv.orderbook_delta),
        "cvd_slope": _fmt(fv.cvd_slope),
        "spread_pct": _fmt(fv.spread_pct),
        "market_quality_score": _fmt(fv.market_quality_score),
        "candle_quality_score": _fmt(fv.candle_quality_score),
        "noise_score": _fmt(fv.noise_score),
        "liquidity_score": _fmt(fv.liquidity_score),
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


def _fmt(value: float | None) -> str:
    """Format a nullable float for prompt injection."""
    if value is None:
        return "N/A"
    return f"{value:.6f}"
