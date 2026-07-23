# AI Interface Specification
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Define the mandatory AI request/response schemas, retry behavior, caching strategy, model selection, and failure modes for all LLM interactions via 9router.

---

## 1. Overview

AI is **mandatory** in two safe positions. This document defines the exact contract for each.

| Position | When | Model | Latency Budget | Failure Mode |
| :--- | :--- | :--- | :--- | :--- |
| Pre-Entry CryptoAnalyst | After deterministic signal, before risk gate | `claude-haiku-3-5` | 15s timeout | Returns 0 conf (REJECT) |
| Position Judge (cheap) | Every 5min in ambiguous zone | `claude-haiku-3-5` | 15s timeout | Escalate to Tier 2 |
| Position Judge (escalated) | After 2× cheap HOLD on losing position | `claude-sonnet-4-5` | 15s timeout | Conservative HOLD |

---

## 2. 9router Proxy Contract

**Endpoint:** `http://127.0.0.1:20129/v1/chat/completions` (OpenAI-compatible)
**Protocol:** HTTP POST, async via `aiohttp`
**Timeout:** 15 seconds (configurable via `SYSTEM_CONSTANTS.md`)
**Retry:** 0 retries (fail fast — AI is mandatory, so signal is rejected on failure)

### Request Format
```json
{
  "model": "claude-haiku-3-5",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "max_tokens": 256,
  "temperature": 0.1
}
```

### Response Format
```json
{
  "choices": [
    {
      "message": {
        "content": "{\"direction\": \"LONG\", \"confidence\": 72, \"reasoning\": \"...\"}"
      }
    }
  ]
}
```

### Error Responses
| HTTP Code | Meaning | Handling |
| :--- | :--- | :--- |
| 200 | Success | Parse JSON from `choices[0].message.content` |
| 408 | Timeout | Log warning, reject signal (analyst) or HOLD (judge) |
| 429 | Rate limited | Log warning, reject signal / HOLD |
| 500 | 9router error | Log error, reject signal / HOLD |
| Connection refused | 9router down | Log error, reject signal / HOLD, trigger AI circuit breaker |

---

## 3. Pre-Entry CryptoAnalyst

### 3.1 Request Schema

**System prompt:** Fixed template. Identifies the AI as a crypto trading analyst.

**User prompt structure:**
```
Analyze this trading opportunity:

Symbol: {symbol}
Regime: {regime}
Global VWAP: {vwap}
Aggregate Skew: {skew}
Lead-Lag Delta: {lead_lag_delta}
Funding Rate: {funding_rate}
OI Change: {oi_change}

TA Indicators (1H):
- RSI(14): {rsi}
- BB Upper/Lower: {bb_upper}/{bb_lower}
- MACD: {macd_signal}
- ATR(14): {atr}
- EMA(20): {ema20}
- EMA(50): {ema50}

Recent trades for {symbol} in {regime}:
{trade_memory_context}

Respond in JSON:
{"direction": "LONG|SHORT|FLAT", "confidence": 0-100, "reasoning": "..."}
```

### 3.2 Response Schema

```json
{
  "direction": "LONG",
  "confidence": 72,
  "reasoning": "Strong bullish skew (0.35) with negative funding (-0.0003) suggesting crowded shorts. RSI at 45 leaves room for upside. Lead-lag confirms Binance leading upward."
}
```

### 3.3 Post-Processing

1. Parse JSON from response (handle markdown code blocks)
2. Normalize confidence: `ai_confidence = parsed_confidence / 100.0`
3. Blend: `final_confidence = quant_confidence * 0.5 + ai_confidence * 0.5`
4. Gate: if `final_confidence < 0.65`, reject signal
5. Cache result in Redis `ai:cache:{hash}` with 300s TTL

### 3.4 Cache Key

Hash of: `{symbol}_{regime}_{skew_bucket}_{funding_bucket}` (rounded to reduce cache misses on minor value changes).

---

## 4. Position Judge

### 4.1 Request Schema (Cheap Tier)

**System prompt:** Fixed template. Identifies the AI as a position management judge.

**User prompt structure:**
```
Evaluate this position:

Symbol: {symbol} | Side: {side}
Entry: {entry_price} | Current: {current_price}
PnL: {pnl_pct}% | Duration: {hold_duration}
ATR: {atr} | Regime: {regime}
Checkpoint: {checkpoint_state}
Consecutive Holds: {consecutive_holds}

Respond in JSON:
{"action": "HOLD|EXIT|TIGHTEN_STOP", "confidence": 0-100, "reasoning": "..."}
```

### 4.2 Request Schema (Escalated Tier)

Same as cheap tier, but adds live TA indicators (RSI, BB, MACD) and requires stronger model (`claude-sonnet-4-5`).

### 4.3 Response Schema

```json
{
  "action": "TIGHTEN_STOP",
  "confidence": 74,
  "reasoning": "RSI showing bearish divergence on 1H while position is in profit. Tightening stop to 1.5x ATR to protect gains."
}
```

### 4.4 Post-Processing

1. Parse JSON from response
2. Apply fail-safe rules:
   - Parse failure → HOLD (never exit without AI)
   - AI unavailable → HOLD
   - 3 consecutive HOLDs on losing position → override to EXIT
3. Execute action (HOLD = no-op, EXIT = market close, TIGHTEN_STOP = amend SL)

---

## 5. Failure Modes

| Failure | Analyst Behavior | Judge Behavior |
| :--- | :--- | :--- |
| 9router timeout (15s) | Reject signal | Conservative HOLD |
| 9router connection refused | Reject signal + trigger AI circuit breaker | HOLD + trigger AI circuit breaker |
| Bad JSON response | Reject signal | HOLD |
| Empty response | Reject signal | HOLD |
| AI confidence < 0.20 (anomaly) | Reject signal + alert | HOLD + alert |
| 3 consecutive failures | Halt all signals (AI mandatory) | HOLD (positions managed by deterministic rules only) |

---

## 6. Model Versioning

| Model | Purpose | Expected Latency | Cost Estimate |
| :--- | :--- | :--- | :--- |
| `claude-haiku-3-5` | Analyst, cheap judge | ~200-400ms | ~$0.50-1.00/day |
| `claude-sonnet-4-5` | Escalated judge | ~800ms | ~$0.10-0.20/day |

Model selection is configurable via `NINE_ROUTER_MODEL` env var (default: `claude-haiku-3-5`). Escalated judge model is hardcoded in `position_judge.py`.

---

## 7. Monitoring

See `METRICS_DICTIONARY.md` §8-§9 for Prometheus metrics:
- `karsa_ai_analyst_calls_total` (labels: result)
- `karsa_ai_analyst_latency_seconds`
- `karsa_ai_analyst_confidence`
- `karsa_position_judge_calls_total` (labels: tier, action)
- `karsa_position_judge_latency_seconds`
