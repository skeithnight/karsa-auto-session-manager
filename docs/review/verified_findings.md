# Verified Findings: Codebase Audit

> Generated from 6 parallel investigations against actual code. Source of truth.

---

## 1. Risk Gate — Hardcoded Values ✅ CONFIRMED

**Call site:** `app/main.py:135-139`
```python
decision = risk_gate.evaluate(
    volume_24h=Decimal("5000000"),  # TODO: real volume from state
    bid_price=Decimal("64000"),     # TODO: real bid from state
    ask_price=Decimal("64100"),     # TODO: real ask from state
)
```

`RiskGate.evaluate()` signature accepts live data — structurally ready. Problem is purely the call site feeding literals.

**Additional finding:** `daily_drawdown_limit` in `gates.py:18` is `float` (`-0.02`), not `Decimal`. Violates CLAUDE.md "No float for money" rule.

---

## 2. Exchange-Side SL — NOT IMPLEMENTED ❌

- `app/execution/bybit_client.py` — no `place_stop_loss()`, no `amend_stop_loss()`. Methods exposed: `connect`, `disconnect`, `set_leverage`, `create_limit_order`, `create_market_order`, `cancel_order`, `amend_order`, `fetch_balance`, `get_wallet_balance`, `fetch_positions`, `fetch_open_orders`, `watch_orders`.
- `app/execution/sor.py` — after every fill (Post-Only/Reprice/Market), returns bare order dict. Zero SL logic.
- **CLAUDE.md Rule 5 violated.** This is the single most safety-critical gap.

---

## 3. Signals — Single-Signal Only ✅ CONFIRMED

**Current formula:** `app/alpha/signals.py:88`
```python
confidence = min(abs(aggregate_skew) / 0.8, 1.0)
```

- No funding rate → `AlphaMetrics` has no `get_funding_rate()` method
- No open interest → `AlphaMetrics` has no `get_open_interest()` method
- Lead-lag computed in `AlphaMetrics.get_lead_lag()` but never called by `SignalGenerator.generate()`
- `exchanges` param in `generate()` is accepted but never read

---

## 4. Redis — In Code, Out of MVP Scope Docs

**Already in codebase:**
- `global:state:{symbol}` — TTL 60s
- `system:heartbeat` — TTL 30s
- `system:circuit_breaker` — no TTL
- `system:config:regime` — no TTL
- `trade:{trade_id}` — no TTL
- `karsa:auto:config`, `karsa:auto:state:active`, `karsa:auto:start_time` — ad-hoc in session.py

**Doc conflict:**
- `DATA_MODEL.md` defines 4 Redis keys
- `ARCHITECTURE.md` omits Redis entirely
- `MVP_SCOPE.md` §3 (in scope) omits Redis; §4 (out of scope) only rejects Redis Pub/Sub split
- `CONTEXT.md §7` marks this as open question — **still unresolved**

**Impact on implementation plan:** Phase 4 (`position_store.py`) needs Redis. Already using Redis for 7+ keys, so adding one more is consistent with code reality — but docs are inconsistent.

---

## 5. Drawdown Conflict — CONFIRMED

| Source | Value | Type |
|---|---|---|
| `app/risk/circuit_breaker.py:18` | `-0.02` (2%) | `Decimal` default |
| `docs/RISK_AND_RUNBOOK.md:34` | `> 3%` | Runtime safety doc |

`main.py:216` instantiates `CircuitBreaker()` with no args — uses 2% code default. No config override exists.

---

## 6. Regime — UI Toggle Only, No Actual Classification

- `system:config:regime` key exists in Redis
- `_toggle_regime()` in `app/bot/handlers.py` toggles on/off via Telegram
- **No Hurst, no ADX, no CHOP detection code exists anywhere in `app/`**
- Phase 1 of implementation plan builds this from scratch — correct assessment

---

## 7. Lead-Lag — Computed, Not Wired

- `app/alpha/metrics.py:48` — `calculate_lead_lag()` pure function (Decimal diff)
- `app/alpha/metrics.py:113` — `get_lead_lag()` method on AlphaMetrics
- Never called by `SignalGenerator.generate()` — confirmed disconnected

---

## 8. OHLCV — No Code Exists

No `ohlcv`, `candle`, `kline`, or `fetch_ohlcv` references in `app/`. `app/data/` uses ccxt for real-time ticker/orderbook only. Phase 2's `OHLCVFetcher` is built from scratch.

---

## 9. Symbols — Config Has 35, MVP Says 5

`app/core/config.py:35` defaults to 35 USDT pairs in 3 tiers. `MVP_SCOPE.md` says Top 5 (BTC, ETH, SOL, BNB, XRP). Config is more aggressive than MVP scope.

---

## 10. Position Store — In-Memory Only

No `position_store.py`. Positions live on `StateManager.positions` dict (in-memory). Phase 4 creates Redis-backed store — new addition, not modifying existing.

---

## Summary: Implementation Plan Claims vs Reality

| Plan Claim | Verified? | Notes |
|---|---|---|
| Hardcoded risk gate values | ✅ Yes | Plan's P1-A accurate |
| No exchange-side SL | ✅ Yes | Plan's P1-B accurate — but this is P0, not P1 |
| No regime classification | ✅ Yes | Plan's Phase 1 builds from scratch |
| Single-signal confidence | ✅ Yes | Plan's Phase 2 replaces correctly |
| Lead-lag computed, not wired | ✅ Yes | Plan wires it correctly |
| `ALPHA_METRICS_SPEC.md` exists | ✅ Yes | At `docs/ALPHA_METRICS_SPEC.md` (not under `docs/review/`) |
| No OHLCV fetcher | ✅ Yes | Plan creates one — correct |
| Redis position store OK | ⚠️ Gray area | Redis already in codebase for 7+ keys; adding position store is consistent with reality, not with docs |
| Win-rate math ~40-58% lift | ❌ Unverifiable | No baseline backtest; stacking independent lifts is wrong |
| Grafana Phase 5 | ⚠️ Scope creep | Plan acknowledges this, proceeds anyway |
