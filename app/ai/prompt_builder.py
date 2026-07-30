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
- ATR (14) %: {atr_pct}
- Hurst exponent: {hurst}

### Trend
- EMA 20: {ema_20}
- EMA 200: {ema_200}
- SMA 20: {sma_20}
- RSI (14): {rsi_14}
- ADX (14): {adx_14}
- Distance from EMA50: {distance_from_ema50_pct}%

### Cross-Asset & Regime
- BTC correlation (30d): {correlation_24h}
- Beta to BTC (30d): {beta_30d}

### Derivatives & Microstructure
- Funding rate: {funding_rate}
- Volume spike ratio: {volume_spike_ratio}x
- Breakout confirmed: {breakout_confirmed}

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
        # Cross-asset & regime features — use defaults if not in FeatureVector
        # These are computed by StatisticalFeatureEngine but not in FeatureVector
        "distance_from_ema50_pct": "N/A",
        "correlation_24h": "N/A",
        "beta_30d": "N/A",
        "volume_spike_ratio": "N/A",
        "breakout_confirmed": "N/A",
    }

    user_prompt = _USER_TEMPLATE.format(
        symbol=context.symbol,
        direction=context.direction,
        regime=context.regime.value,
        **feature_map,
    )

    # DEBUG: log features being sent to AI
    import logging
    logger = logging.getLogger("karsa.ai.prompt")
    logger.debug(
        f"PromptBuilder: features for {context.symbol} — "
        f"close={feature_map.get('close')} rsi={feature_map.get('rsi_14')} "
        f"atr={feature_map.get('atr')} adx={feature_map.get('adx_14')} "
        f"regime={context.regime.value} direction={context.direction}"
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
