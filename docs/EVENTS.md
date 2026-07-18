# Event Contracts
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Define every in-process event (asyncio.Queue messages, Redis state updates, kill switch, watchdog triggers, Telegram commands) with producer, consumer, payload, idempotency, and ordering guarantees.
**Last Revised:** 2026-07-17 — WARP→WireGuard cleanup

---

## 1. In-Process Event Queues

The system uses two `asyncio.Queue` objects to pipeline signals through the 6-stage lifecycle.

### 1.1 `signal_queue` (Alpha Bridge → Risk Gate)

| Field | Value |
| :--- | :--- |
| **Producer** | `alpha_bridge_task` (`app/main.py`) |
| **Consumer** | `risk_gate_task` (`app/main.py`) |
| **Payload type** | `TradingSignal` (Pydantic model) |
| **Max size** | Unbounded (default `asyncio.Queue()`) |
| **Ordering** | FIFO — signals processed in generation order |
| **Idempotency** | Not idempotent — each signal processed exactly once. If risk gate rejects, signal is dropped (not retried). |
| **Backpressure** | None — producer never blocks. If consumer is slow, signals queue up. |
| **Lifecycle** | Always running (data pipeline). Signals are generated regardless of ASM state. |

**Payload fields:**
```json
{
  "symbol": "BTC/USDT",
  "direction": "LONG",
  "confidence_score": 0.72,
  "timestamp": "2024-01-15T14:30:00Z",
  "metadata": {"regime": "TREND_BULL", "skew": 0.35, "lead_lag": 0.002}
}
```

### 1.2 `risk_queue` (Risk Gate → Executor)

| Field | Value |
| :--- | :--- |
| **Producer** | `risk_gate_task` (`app/main.py`) |
| **Consumer** | `executor_task` (`app/main.py`) |
| **Payload type** | `TradingSignal` (same model, enriched with risk decision) |
| **Max size** | Unbounded |
| **Ordering** | FIFO |
| **Idempotency** | Not idempotent — each signal processed exactly once. |
| **Backpressure** | None. If executor is slow, signals queue up. |
| **Lifecycle** | Signals are queued regardless of ASM state. Executor only processes when ASM session is active. Signals may accumulate in queue while ASM is inactive. |

---

## 2. Redis State Events

Components communicate via Redis keys (not pub/sub). Each key has a single writer and zero or more readers.

### 2.1 GlobalState Update

| Field | Value |
| :--- | :--- |
| **Writer** | `_stream_orderbook` (data engine task) |
| **Readers** | `alpha_bridge_task`, `risk_gate_task`, `_get_price` |
| **Key pattern** | `global:state:{symbol}` |
| **TTL** | 60 seconds |
| **Update frequency** | Every WebSocket tick (~100ms per exchange) |
| **Payload** | JSON string (see `DATA_MODEL.md` `GlobalStateCache`) |

### 2.2 Regime Update

| Field | Value |
| :--- | :--- |
| **Writer** | `regime_engine_task` |
| **Readers** | `alpha_bridge_task` |
| **Key** | `system:config:regime` |
| **TTL** | None (persists until overwritten) |
| **Update frequency** | Every 15 minutes |
| **Payload** | `{"regime": "TREND_BULL", "hurst": 0.58, "adx": 28.5}` |

### 2.3 Universe Refresh

| Field | Value |
| :--- | :--- |
| **Writer** | `universe_refresh_task` (planned) |
| **Readers** | `data_engine_task`, `alpha_bridge_task` |
| **Key** | `system:universe:symbols` |
| **TTL** | None |
| **Update frequency** | Every 4 hours |
| **Payload** | `{"symbols": ["BTC/USDT", ...], "scores": {"BTC/USDT": 82, ...}, "updated_at": "..."}` |

### 2.4 AI Cache

| Field | Value |
| :--- | :--- |
| **Writer** | `CryptoAnalyst.analyze()` |
| **Readers** | `CryptoAnalyst.analyze()` (cache check) |
| **Key pattern** | `ai:cache:{hash}` |
| **TTL** | 300 seconds (5 min) |
| **Payload** | `{"direction": "LONG", "confidence": 72, "reasoning": "...", "model": "claude-haiku-3-5"}` |

### 2.5 Position State

| Field | Value |
| :--- | :--- |
| **Writer** | `PositionStore` (on fill/exit) |
| **Readers** | `TrailingStopManager`, `CheckpointManager`, `PositionJudge` |
| **Key pattern** | `karsa:position:{symbol}:{side}` |
| **TTL** | None (cleaned up on exit) |
| **Payload** | Hash: `{entry_price, peak_price, atr, sl_order_id, checkpoint, entry_time}` |

### 2.6 Trade Memory

| Field | Value |
| :--- | :--- |
| **Writer** | `CheckpointManager._exit_position()` (planned) |
| **Readers** | `CryptoAnalyst.analyze()` (prompt injection) |
| **Key pattern** | `karsa:memory:{symbol}` |
| **TTL** | None |
| **Data structure** | Redis Sorted Set, score=UNIX timestamp, member=JSON string |
| **Max entries** | 20 per symbol (FIFO eviction) |

---

## 3. Kill Switch Event

| Field | Value |
| :--- | :--- |
| **Trigger sources** | Telegram `/kill_karsa`, file flag `/tmp/KILL_KARSA`, SIGINT, SIGTERM |
| **Event type** | `asyncio.Event` (`kill_switch`) |
| **Consumers** | All tasks (checked in main loop) |
| **Sequence** | Cancel all orders → Market flatten → Set event → Alert Telegram → Exit |
| **Timeout** | < 10 seconds total |

---

## 4. Watchdog Events

### 4.1 Stale Data Event

| Field | Value |
| :--- | :--- |
| **Producer** | Watchdog heartbeat monitor |
| **Consumer** | Alpha bridge (pauses signal generation) |
| **Mechanism** | `asyncio.Event` (`data_stale`) |
| **Trigger** | Any exchange WS silent for > 10 seconds |
| **Recovery** | Auto-resumes when heartbeats recover |

### 4.2 Latency Spike Event

| Field | Value |
| :--- | :--- |
| **Producer** | Watchdog latency tracker |
| **Consumer** | SOR (`skip_to_market = True`) |
| **Mechanism** | Direct attribute set on SOR instance |
| **Trigger** | Average execution latency > 1500ms over 5-min window |
| **Recovery** | Auto-recovers when latency drops |

### 4.3 Event Loop Lag Event

| Field | Value |
| :--- | :--- |
| **Producer** | Watchdog loop lag monitor |
| **Consumer** | Kill switch (triggers flatten + shutdown) |
| **Mechanism** | Calls `sor.cancel_all_positions()` then sets `kill_switch` |
| **Trigger** | Loop lag > 100ms for 3 consecutive checks (30s) |
| **Recovery** | None — process exits |

---

## 5. Telegram Bot Events

### 5.1 Inbound Commands

| Command | Handler | Authorization | Priority |
| :--- | :--- | :--- | :--- |
| `/kill_karsa` | `handle_kill` | Required | CRITICAL |
| `/clear_halt` | `handle_clear_halt` | Required | CRITICAL |
| `/status` | `handle_status` | Required | NORMAL |
| `/positions` | `handle_positions` | Required | NORMAL |
| `/pnl` | `handle_pnl` | Required | NORMAL |
| `/risk` | `handle_risk` | Required | NORMAL |
| `/universe` | `handle_universe` | Required | NORMAL |
| `/ai` | `handle_ai_status` | Required | NORMAL |
| `/settings` | `handle_settings` | Required | NORMAL |
| `/alerts` | `handle_alerts_toggle` | Required | NORMAL |

### 5.2 Outbound Alerts

| Alert Type | Trigger | Priority | Always Send? |
| :--- | :--- | :--- | :--- |
| Kill switch activated | Kill switch triggered | CRITICAL | Yes |
| Circuit breaker tripped | Daily drawdown / consecutive losses | CRITICAL | Yes |
| Proxy degraded | VPN tunnel latency > 2000ms | WARNING | Yes |
| Stale data | WS silent > 15s | WARNING | Yes |
| AI offline | 9router unreachable 3× | CRITICAL | Yes |
| Position opened | SOR fill confirmed | INFO | If alerts_enabled |
| Position closed | Checkpoint/exit executed | INFO | If alerts_enabled |
| AI rejected signal | AI confidence below threshold | AI_DECISION | If alerts_enabled |
| AI judge verdict | Position judge HOLD/EXIT/TIGHTEN | AI_DECISION | If alerts_enabled |
| Universe refreshed | Universe scorer update | UNIVERSE | If alerts_enabled |
| Sector cap rejected | Sector diversity cap blocks trade | UNIVERSE | If alerts_enabled |
