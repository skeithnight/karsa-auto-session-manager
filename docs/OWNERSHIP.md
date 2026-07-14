# Domain Object Ownership
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Define which component owns each domain object — who creates, reads, updates, and deletes it. Prevents two components from fighting over the same state.

---

## 1. Ownership Rules

1. **Each domain object has exactly one owner.** The owner is the only component that creates, updates, and deletes the object.
2. **Other components are consumers (read-only).** If a consumer needs to modify state, it must request the owner to do so.
3. **Cross-component communication** happens via Redis keys (async) or asyncio.Queue (in-process), never by direct method calls across key boundaries.

---

## 2. Domain Object Ownership Table

| Domain Object | Owner | Creates | Updates | Deletes | Consumers |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **GlobalState** (per-symbol market snapshot) | Data Engine | On WS tick | On WS tick | TTL auto-expire (60s) | Alpha Bridge, Risk Gate, Bot |
| **Regime** (market classification) | Regime Engine | On classification | On classification (every 15min) | Never (overwrites) | Alpha Bridge, Entry Filter, Bot |
| **TradingSignal** (directional signal) | Alpha Bridge | On signal generation | Never (immutable) | Dropped if rejected | Risk Gate, Executor |
| **RiskDecision** (gate pass/fail) | Risk Gate | On evaluation | Never (immutable) | Dropped after executor consumes | Executor |
| **TradeExecution** (order result) | Executor (SOR) | On order fill (ASM active only) | Never (immutable) | Never | State Manager, Position Store, Bot |
| **Position** (open position state) | Position Store | On fill | On trailing stop/checkpoint | On exit | Trailing Stop, Checkpoint Manager, Judge, Bot |
| **Stop-Loss** (exchange-side SL) | Bybit Client | On fill (immediate) | On trailing stop amend | On position close | Trailing Stop (via amend) |
| **Circuit Breaker** (halt state) | Circuit Breaker | On breach | On reset | Never (overwrites) | Risk Gate, Bot |
| **Universe** (active symbols) | Universe Scorer (planned) | On refresh | On refresh (every 4h) | Never (overwrites) | Data Engine, Alpha Bridge, Bot |
| **Trade Memory** (historical trades) | Checkpoint Manager (planned) | On position close | Never (append-only) | FIFO eviction (max 20) | AI Analyst |
| **AI Result** (analyst/judge output) | AI Analyst / Judge | On LLM call | Never (immutable) | TTL auto-expire (300s) | Alpha Bridge, Checkpoint Manager |
| **Sector Count** (positions per sector) | Sector Cap (planned) | On position open | On position open/close | On position close | Executor |
| **Bot Settings** (user preferences) | Telegram Bot | On `/settings` callback | On `/settings` callback | Never | All (read via Redis) |
| **Session Config** (autonomous session) | Session Manager | On session start | On session update | On session stop | Bot, Session Manager |

---

## 3. Ownership by Component

### 3.1 Data Engine (`app/data/`)

**Owns:**
- GlobalState (per-symbol aggregated market snapshot)
- Per-exchange heartbeats (`system:heartbeats` hash)

**Does NOT own:**
- Regime (owned by Regime Engine)
- Universe (owned by Universe Scorer)
- Any execution state

### 3.2 Alpha Bridge (`app/alpha/`)

**Owns:**
- TradingSignal (created, then consumed by Risk Gate)
- AI Analyst results (cached in Redis)

**Does NOT own:**
- GlobalState (reads from Data Engine)
- Regime (reads from Regime Engine)
- Position state (reads from Position Store)

### 3.3 Risk Gate (`app/risk/`)

**Owns:**
- RiskDecision (pass/fail result)
- Circuit Breaker state

**Does NOT own:**
- TradingSignal (reads from Alpha Bridge)
- Position state (reads from Position Store)

### 3.4 Executor (`app/execution/`)

**Owns:**
- TradeExecution (order fill result)
- Stop-Loss orders (exchange-side)
- Position state (via Position Store)

**Does NOT own:**
- TradingSignal (reads from Risk Gate)
- RiskDecision (reads from Risk Gate)

### 3.5 Position Store (`app/core/position_store.py`)

**Owns:**
- Position lifecycle state (`karsa:position:{symbol}:{side}`)
- Trade memory (`karsa:memory:{symbol}`) — planned

**Does NOT own:**
- Trade executions (Postgres `trades` table)
- Stop-Loss orders (exchange-side, owned by Bybit Client)

### 3.6 State Manager (`app/core/state.py`)

**Owns:**
- In-memory position/order cache
- Postgres trade/signal/event records

**Does NOT own:**
- Redis state (owned by individual components)
- Exchange state (source of truth is Bybit REST API)

### 3.7 Telegram Bot (`app/bot/`)

**Owns:**
- Bot settings (`karsa:settings:*`, `karsa:alerts_enabled`)
- Emergency halt flag (`karsa:global_halt`)
- Session config (`karsa:auto:config`, `karsa:auto:state:active`)

**Does NOT own:**
- Trading state (reads from all components)
- Position state (reads from Position Store)

### 3.8 Watchdog (`app/watchdog/`)

**Owns:**
- Nothing (pure consumer)

**Reads:**
- Heartbeats (`system:heartbeat`, `system:heartbeats`)
- Execution latency (from SOR)
- Event loop lag (measured directly)

---

## 4. Ownership Transfer Scenarios

### 4.1 Signal Lifecycle
```
Alpha Bridge (creates) → signal_queue → Risk Gate (reads, creates RiskDecision) → risk_queue → Executor (reads, creates TradeExecution) → Position Store (creates Position)
```

### 4.2 Position Lifecycle
```
Executor (creates Position via Position Store) → Trailing Stop (updates peak/SL) → Checkpoint Manager (updates checkpoint) → Exit (deletes Position via Position Store)
```

### 4.3 Kill Switch
```
Telegram Bot / SIGINT (creates halt flag) → All tasks (read halt flag) → Executor (flattens positions) → Process exits
```

---

## 5. Violations to Watch For

- **Two components writing same Redis key:** e.g., both Alpha Bridge and Risk Gate writing `global:state:{symbol}` — bug.
- **Consumer modifying owner's state directly:** e.g., Checkpoint Manager calling `position_store.update_sl()` instead of requesting Trailing Stop to amend — design violation.
- **Stale ownership after crash:** If Executor crashes mid-trade, Position Store may have a position that Bybit doesn't know about. Reconciliation resolves this on restart.
