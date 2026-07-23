# Alpha Metrics Specification
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed (fills a gap: `PRD.md`/`ARCHITECTURE.md` name these metrics but never define the formula)
**Purpose:** Give each Alpha Bridge (Key 2) metric an exact, testable formula so `DEFINITION_OF_DONE.md`'s requirement of "hand-calculated JSON fixtures" is actually possible to satisfy.

---

## 1. Inputs Available (from `DATA_MODEL.md`)

Everything below must be derivable from the `GlobalStateCache` schema as currently defined:

```json
{
  "symbol": "BTC/USDT",
  "global_vwap": "...",
  "global_skew": 0.0,
  "global_funding_avg": "...",
  "prices": {"binance": "...", "okx": "...", "bybit": "..."},
  "volumes_24h": {"binance": "...", "okx": "...", "bybit": "..."},
  "last_update_utc": "...",
  "status": "ACTIVE | STALE | DEGRADED"
}
```

**Flag (upfront):** this schema has per-exchange *price* and *24h volume*, but no order-book depth fields (no bid/ask volume). `global_skew` cannot be computed from this schema as-is — see §3.

---

## 2. Global VWAP

**Definition used here:** a 24h-volume-weighted composite price across `ACTIVE` exchanges — *not* a rolling trade-tape VWAP, because the current schema has no trade tape, only point-in-time prices + static 24h volume.

```
GlobalVWAP = Σ(price_i × volume_24h_i) / Σ(volume_24h_i)   for all i where status == ACTIVE
```

- Exchanges marked `STALE` are excluded entirely (per `DEFINITION_OF_DONE.md` §3.B: "Calculates metrics using only `ACTIVE` exchange data").
- If all exchanges are `STALE`, `GlobalVWAP` is undefined — the Alpha Bridge must emit `FLAT` and skip signal generation rather than divide by zero.

**Worked example:**
| Exchange | Price | 24h Volume | Status |
| :--- | :--- | :--- | :--- |
| Binance | 64,252.00 | 1,250,000,000 | ACTIVE |
| OKX | 64,251.50 | 850,000,000 | ACTIVE |
| Bybit | 64,245.00 | 420,000,000 | STALE (excluded) |

```
GlobalVWAP = (64252.00 × 1,250,000,000 + 64251.50 × 850,000,000) / (1,250,000,000 + 850,000,000)
           = 64,251.80  (rounded to 2 dp)
```

**Open question:** if a true rolling-window (e.g. 15-min trade-weighted) VWAP is actually wanted — which `ARCHITECTURE.md` §4.B's "15m/1h rolling windows" language hints at — the Data Engine needs to ingest and buffer a trade tape, and `DATA_MODEL.md` needs a new field for it. Until that's decided, this static composite-price formula is the implementable spec.

---

## 3. Global Order Book Skew

**Gap:** `global_skew` is described in `PRD.md`/`ARCHITECTURE.md` as "Aggregate Order Book Skew (Bid vs. Ask volume ratio)," but `GlobalStateCache` in `DATA_MODEL.md` has no bid/ask depth fields to compute it from. This metric **cannot be built against the current locked schema.**

**Proposed formula (pending schema extension):**

```
Skew = Σ(bid_depth_i) / (Σ(bid_depth_i) + Σ(ask_depth_i))   for all i where status == ACTIVE
```

- Range: `0.0`–`1.0`, matching the existing `global_skew: float = Field(..., ge=0.0, le=1.0)` constraint in `DATA_MODEL.md`.
- `0.5` = balanced book. `> 0.5` = bid-heavy (bullish pressure). `< 0.5` = ask-heavy (bearish pressure).
- **Depth window (needs a decision):** top-N price levels vs. depth within X% of mid-price. Recommend starting with **top 20 levels per exchange**, configurable via `system:config:regime`.

**Required schema addition** (proposed, not yet part of locked `DATA_MODEL.md`):
```json
{
  "bid_depth": {"binance": "...", "okx": "...", "bybit": "..."},
  "ask_depth": {"binance": "...", "okx": "...", "bybit": "..."}
}
```
This should be raised as an addendum to `DATA_MODEL.md` before Phase 3 (Alpha & Risk) begins — see `MVP_SCOPE.md` Phase 3.

---

## 4. Global Funding Rate Divergence

```
FundingDivergence = bybit_local_funding_rate − global_funding_avg
```

Where `global_funding_avg` is the volume-weighted average funding rate across `ACTIVE` read exchanges (same weighting pattern as §2), and `bybit_local_funding_rate` comes from Bybit's own public feed (Bybit is both a read source and the write venue).

- **Signal trigger (proposed, needs calibration):** flag "divergence" when `|FundingDivergence| > 0.0003` (3 bps) — i.e., global sentiment and Bybit's local funding disagree meaningfully. This threshold is **not specified anywhere in the locked docs** and should be treated as a tunable config value (`system:config:regime`), not a hardcoded constant.
- Directional read: if global funding is heavily negative (shorts paying longs, i.e. crowded short) while Bybit's local funding is neutral, that's the "macro squeeze" setup described in `PRD.md` §5 — biases toward `LONG`.

---

## 5. Lead-Lag Signal

Per `MVP_SCOPE.md` §3.C: "Simple threshold logic comparing Binance price movement against Bybit price movement on a 15-minute rolling window."

```
binance_return_15m = (binance_price_now − binance_price_15m_ago) / binance_price_15m_ago
bybit_return_15m   = (bybit_price_now − bybit_price_15m_ago) / bybit_price_15m_ago
lead_lag_delta     = binance_return_15m − bybit_return_15m
```

- If `lead_lag_delta > threshold_bps` → Binance has moved further than Bybit → bias `LONG` (expect Bybit to catch up).
- If `lead_lag_delta < −threshold_bps` → bias `SHORT`.
- Else → no lead-lag signal (`FLAT` contribution).
- **`threshold_bps` is not specified in any locked doc.** Propose a starting default of **10 bps** as a config value, to be empirically calibrated during Phase 3 testnet runs — not hardcoded.
- Requires the Data Engine to retain a 15-minute rolling price buffer per exchange (not currently in `GlobalStateCache`, which is point-in-time). This buffer can live in-process (e.g., a deque per symbol/exchange) without necessarily needing a schema change, since it's transient working state, not persisted state.

---

## 6. Bad Tick Filter (restated as formula, per `ARCHITECTURE.md` §4.A / `DEFINITION_OF_DONE.md` §3.A)

```
reject_tick if |price_t − price_(t−1)| / price_(t−1) > 0.05   AND   (t − t−1) < 1 second
```

Applies per-exchange, before any value enters `GlobalVWAP`/`Skew` aggregation. A rejected tick does not update `prices[exchange]`; the last good price is retained until the next valid tick or the exchange is marked `STALE` after 15s of no valid updates.

---

## 7. Confidence Score

`TradingSignal.confidence_score` (0.0–1.0) exists in `DATA_MODEL.md` §4.

**Current implementation:** `min(abs(aggregate_skew) / 0.8, 1.0)` — single-signal, hardcoded divisor. See `app/alpha/signals.py:88`.

**Proposed (Phase 2 of execution plan):** weighted composite with regime modifier:

```
confidence = regime_mult × (w_skew × S_skew + w_lead_lag × S_lead_lag + w_funding × S_funding + w_oi × S_oi)
```

| Signal | Weight | Formula | Direction |
| :--- | :--- | :--- | :--- |
| `S_skew` | 0.40 | `min(1, \|aggregate_skew\| / 0.8)` | `skew > 0 → LONG` |
| `S_lead_lag` | 0.30 | `min(1, \|delta\| / lead_lag_ceiling)` | `binance_leads_up → LONG` |
| `S_funding` | 0.20 | `min(1, \|funding_rate\| / 0.0003)` | `funding < 0 → LONG (contrarian)` |
| `S_oi` | 0.10 | `0.0 or 1.0` (binary: OI rising = 1) | confirms momentum |

**Regime modifier:**
- TREND_BULL: `+0.10`
- TREND_BEAR: `+0.05`
- MEAN_REVERSION: `-0.15`
- CHOP: `× 0.0` (force FLAT)

**Direction logic:** AND-gate for MVP (all 3 directional signals agree). Loosened to majority-vote in V1.1.
**Minimum confidence gate:** `0.65`

**AI-Mandatory Final Confidence:**
The deterministic composite above produces `quant_confidence`. The AI CryptoAnalyst then produces `ai_confidence` (0–100, normalized to 0.0–1.0). Final confidence:
```
final_confidence = quant_confidence × 0.5 + ai_confidence × 0.5
```
Gate: `final_confidence >= 0.65` → signal fires. **If AI call fails, signal is rejected** (mandatory means mandatory — no bypass).

**Status:** Implemented. AI is mandatory, not optional. See `docs/review/ai_layer_analysis.md`.

---

## 8. Signal Assembly (Direction Logic)

MVP Scope calls for a "simple directional signal" — proposed as an **AND-gate** for the MVP (all contributing signals must independently agree on direction, or the output is `FLAT`), rather than a weighted blend, to keep the first version deterministic and easy to unit test:

```
IF skew_direction == lead_lag_direction == funding_direction == LONG:  → LONG
IF skew_direction == lead_lag_direction == funding_direction == SHORT: → SHORT
ELSE: → FLAT
```

A weighted/majority-vote scheme can replace this in V1.1 once there's enough logged `signals` table data (per `DATA_MODEL.md`) to evaluate which blending approach actually performs better — but that comparison requires the AND-gate baseline to exist first.

---

## 9. Test Fixture Requirement

Every formula above needs at least one hand-calculated static JSON fixture in `tests/fixtures/alpha/` (see `TESTING_STRATEGY.md` §3/§8) before the corresponding code is considered done, per `DEFINITION_OF_DONE.md` §2.

---

## 10. AI Layer Integration (Mandatory)

**Reference:** `docs/review/ai_layer_analysis.md` for latency math and safe-position rationale.

### 10.1 Two Safe Positions

| Position | When | Model | Latency | Failure Mode |
| :--- | :--- | :--- | :--- | :--- |
| **Pre-Entry CryptoAnalyst** | After deterministic signal, before risk gate | `claude-haiku-3-5` | ~400ms | Returns 0 conf (REJECT) |
| **Position Judge (cheap)** | Every 5min in ambiguous zone | `claude-haiku-3-5` | ~200ms | Escalate to Tier 2 |
| **Position Judge (escalated)** | After 2× cheap HOLD on losing position | `claude-sonnet-4-5` | ~800ms | Conservative HOLD |

### 10.2 Pre-Entry CryptoAnalyst Prompt Structure

Input to 9router (`app/core/ai_client.py`):
- TA indicators: RSI(14), BB(20), MACD, ATR(14), EMA(20/50) — computed by `ta_tools.py`
- Regime state (TREND_BULL/BEAR/MEAN_REVERSION)
- GlobalState: VWAP, skew, lead-lag delta, funding rate, OI change
- Trade memory context (last 3 similar trades — see §10.4)
- Confidence threshold: 0.65

Output: JSON `{direction: LONG|SHORT|FLAT, confidence: 0-100, reasoning: "..."}`

### 10.3 Position Judge Prompt Structure

Input: position metadata (entry price, current PnL, hold duration, ATR, checkpoint state). No TA tools in cheap pass. Escalated pass includes live market data.

Output: JSON `{action: HOLD|EXIT|TIGHTEN_STOP, confidence: 0-100, reasoning: "..."}`

Fail-safe: 3 consecutive HOLDs on losing position → forced EXIT regardless of AI verdict.

### 10.4 Trade Memory Injection

Storage: Redis sorted set `karsa:memory:{symbol}`, score=timestamp. Each entry:
```json
{"pnl_pct": 1.2, "hold_min": 45, "regime": "TREND_BULL", "exit": "trailing_stop", "confidence": 0.72}
```
Retrieval: last 3 trades matching same symbol + regime. Formatted as prompt prefix:
```
Recent trades for BTC/USDT in TREND_BULL:
1. +1.2% (45min, trailing stop, conf=0.72)
2. -0.8% (2h, hard fail, conf=0.68)
3. +2.1% (3h, checkpoint, conf=0.81)
```

### 10.5 9router Proxy

Endpoint: `http://127.0.0.1:20129/v1/chat/completions` (OpenAI-compatible).
Client: `app/core/ai_client.py` — async HTTP with retry, 15s timeout.
Cache: Analyst results cached in Redis `ai:cache:*` with 5min TTL.
Cost estimate: ~$0.60–1.20/day at 5 symbols, 15-min scan cadence.