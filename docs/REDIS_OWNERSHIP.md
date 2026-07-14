# Redis Key Ownership
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Single canonical table of every Redis key pattern, its single writer, its readers, TTL, and data contract. Enforces the "single-writer" invariant — if two components write the same key, that's a bug.

---

## 1. Ownership Rules

1. **Every key has exactly one writer.** If a key needs a second writer, that's a design change — flag it.
2. **Readers are listed but not restricted.** Any component can read any key.
3. **TTL is mandatory for ephemeral state.** Position keys survive until explicit cleanup (no TTL). Cache keys must have TTL.
4. **Key patterns use `{symbol}` or `{side}` placeholders.** Literal key examples are in `DATA_MODEL.md`.

---

## 2. Key Ownership Table

| Key Pattern | Writer | Readers | TTL | Data Format | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `global:state:{symbol}` | Data Engine (`_stream_orderbook`) | Alpha Bridge, Risk Gate, Bot (`/positions`) | 60s | JSON string (GlobalStateCache) | Real-time aggregated market state |
| `system:heartbeat` | Data Engine (`_stream_orderbook`) | Watchdog | 30s | ISO 8601 string | Main process liveness |
| `system:heartbeats` | Data Engine (`_stream_orderbook`) | Watchdog | None | Hash: field=exchange, value=ISO timestamp | Per-exchange heartbeat |
| `system:circuit_breaker` | Risk Gate (`CircuitBreaker`) | Risk Gate Task, Bot (`/risk`) | None | JSON: `{status, reason, triggered_at}` | Global halt state |
| `system:config:regime` | Regime Engine (`regime_engine_task`) | Alpha Bridge, Bot (`/status`) | None | JSON: `{regime, hurst, adx}` | Current market regime |
| `system:universe:symbols` | Universe Scorer (planned) | Data Engine, Alpha Bridge, Bot (`/universe`) | None | JSON: `{symbols, scores, updated_at}` | Active tradeable symbols |
| `karsa:position:{symbol}:{side}` | Position Store | Trailing Stop, Checkpoint Manager, Bot (`/positions`) | None | Hash: `{entry_price, peak_price, atr, sl_order_id, checkpoint, entry_time}` | Position lifecycle state |
| `trade:{trade_id}` | State Manager | Bot (`/pnl`) | None | JSON string | Completed trade record |
| `karsa:memory:{symbol}` | Checkpoint Manager (planned) | AI Analyst (planned) | None | Sorted Set: score=timestamp, member=JSON | Trade memory for AI context |
| `karsa:sector:{sector_name}` | Sector Cap (planned) | Executor Task (planned) | None | Integer string | Active position count per sector |
| `ai:cache:{hash}` | AI Analyst (`CryptoAnalyst`) | AI Analyst (cache check) | 300s | JSON: `{direction, confidence, reasoning, model}` | AI result cache |
| `karsa:global_halt` | Bot (`/kill_karsa`, `/clear_halt`) | Risk Gate Task | None | `"1"` = halt active | Emergency halt flag |
| `karsa:alerts_enabled` | Bot (`/alerts`) | Bot (alert dispatch) | None | `"1"` / `"0"` | Alert toggle |
| `karsa:auto:state:active` | Bot (settings) | Bot (status) | None | `"1"` / `"0"` | Session active flag |
| `karsa:auto:config` | Bot (settings) | Bot (status) | None | JSON: `{risk_pct, max_pos, interval_min, duration_min}` | Session parameters |
| `karsa:auto:start_time` | Bot (settings) | Bot (status) | None | UNIX timestamp string | Session start time |
| `karsa:settings:max_positions` | Bot (settings callback) | Bot (settings) | None | `"3"` / `"5"` / `"8"` | Max positions preference |
| `karsa:settings:regime_filter` | Bot (settings callback) | Bot (settings), Alpha Bridge | None | `"1"` / `"0"` | Regime filter toggle |
| `karsa:state:risk_profile` | Bot (settings callback) | Bot (settings), Risk Gate | None | `"conservative"` / `"semi_aggressive"` / `"aggressive"` | Risk profile |
| `karsa:crypto_cooldown` | Bot (`/sell_all`) | Alpha Bridge | 900s | `"1"` | 15-min post-sell cooldown |

---

## 3. Key Lifecycle

### Created at startup
- `system:heartbeat` — written every 10s by data engine
- `system:heartbeats` — written every WS tick by data engine
- `system:circuit_breaker` — initialized by risk gate

### Created on signal
- `global:state:{symbol}` — written on every WS tick, auto-expires after 60s
- `ai:cache:{hash}` — written on AI analyst call, auto-expires after 300s

### Created on trade
- `karsa:position:{symbol}:{side}` — created on fill, updated on trailing stop/checkpoint, deleted on exit
- `trade:{trade_id}` — created on trade close, never expires
- `karsa:memory:{symbol}` — appended on trade close, never expires (max 20 entries per symbol)

### Created by operator
- `karsa:global_halt` — set by `/kill_karsa`, cleared by `/clear_halt`
- `karsa:auto:state:active` — set by bot settings
- `karsa:settings:*` — set by bot settings callbacks

---

## 4. Violations to Watch For

- **Two writers on same key:** If both `alpha_bridge_task` and `risk_gate_task` write `global:state:{symbol}`, that's a bug.
- **Missing TTL on cache keys:** If `ai:cache:*` has no TTL, memory grows unbounded.
- **Stale position keys:** If `karsa:position:{symbol}:{side}` exists but Bybit says flat, that's a ghost position — reconciliation must clean it up.
- **Orphaned halt flag:** If `karsa:global_halt` is set but kill switch never fired, trading is silently blocked.
