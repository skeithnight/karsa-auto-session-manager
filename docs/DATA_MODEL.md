# Data Model & Schema Dictionary
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Locked  
**Purpose:** Define exact data structures, serialization rules, and storage schemas across the entire pipeline.

---

## 1. Serialization & Type Rules (CRITICAL)
Before defining schemas, these rules **must** be enforced across all components:
1. **Never use `float` for prices or sizes.** Always use `decimal.Decimal` in Python to prevent floating-point precision loss.
2. **JSON/Redis Serialization:** When converting `Decimal` to JSON/Redis, serialize as strings (`"64250.50"`). Parse back to `Decimal` on read.
3. **Timestamps:** Always use UTC (`datetime.datetime.now(timezone.utc)`). Store as ISO 8601 strings in Redis/JSON, and `TIMESTAMPTZ` in PostgreSQL.
4. **Immutability:** Internal Pydantic models should be treated as immutable. Create new instances for state updates rather than mutating in-place.

---

## 2. Redis Cache Keys (Fast State & Health)
Redis is used for high-speed state persistence, cross-component caching, and Watchdog health checks. All values are JSON-encoded strings unless noted.

| Key Pattern | Type | TTL | Structure (JSON) | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `global:state:{symbol}` | String | 60s | See `GlobalStateCache` below | Real-time aggregated market state per symbol |
| `system:heartbeat` | String | 30s | `"2024-01-15T14:30:00Z"` | Watchdog liveness ping |
| `system:circuit_breaker` | String | None | `{"status": "ACTIVE", "reason": null, "triggered_at": null}` | Global halt state |
| `system:config:regime` | String | None | `{"regime": "TREND_BULL", "hurst": 0.58, "adx": 28.5}` | BTC regime classification output (Hurst + ADX) |
| `system:universe:symbols` | String | None | `{"symbols": ["BTC/USDT", ...], "scores": {"BTC/USDT": 82, ...}, "updated_at": "..."}` | Universe scorer output — active tradeable symbols |
| `system:heartbeats` | Hash | None | field=exchange, value=ISO timestamp | Per-exchange heartbeat timestamps |
| `karsa:memory:{symbol}` | Sorted Set | None | score=UNIX timestamp, member=JSON string | Trade memory for AI context injection |
| `karsa:sector:{sector_name}` | String | None | `"2"` | Active position count per sector |
| `ai:cache:{hash}` | String | 300s | `{"direction": "LONG", "confidence": 72, "reasoning": "..."}` | AI analyst result cache (5min TTL) |
| `karsa:position:{symbol}:{side}` | Hash | None | `{entry_price, peak_price, atr, sl_order_id, checkpoint, ...}` | Position lifecycle state |
| `trade:{trade_id}` | Hash | None | `{symbol, side, entry_price, exit_price, pnl_usdt, status, ...}` | Per-trade lifecycle snapshot (State Manager owned) |

### `GlobalStateCache` Schema
```json
{
  "symbol": "BTC/USDT",
  "global_vwap": "64250.50",
  "global_skew": 0.68,
  "global_funding_avg": "0.00012",
  "prices": {
    "binance": "64252.00",
    "okx": "64251.50",
    "bybit": "64245.00"
  },
  "volumes_24h": {
    "binance": "1250000000",
    "okx": "850000000",
    "bybit": "420000000"
  },
  "last_update_utc": "2024-01-15T14:30:00.123Z",
  "status": "ACTIVE"
}
```

---

## 3. PostgreSQL Schema (Audit & Analytics)
All tables use `UUID` primary keys, `TIMESTAMPTZ` for timestamps, and `JSONB` for complex snapshots. Indexes are pre-defined for query performance.

### Table: `trades`
Stores the complete lifecycle of every executed order.
```sql
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    size DECIMAL(20,8) NOT NULL,
    entry_price DECIMAL(20,8) NOT NULL,
    exit_price DECIMAL(20,8),
    pnl_usdt DECIMAL(20,8),
    execution_latency_ms INTEGER,
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'FILLED', 'PARTIAL', 'CANCELLED', 'FAILED')),
    risk_snapshot JSONB NOT NULL,
    global_state_snapshot JSONB NOT NULL,
    order_id VARCHAR(50),
    exchange_order_id VARCHAR(50)
);

CREATE INDEX idx_trades_timestamp ON trades(timestamp DESC);
CREATE INDEX idx_trades_symbol_status ON trades(symbol, status);
```

### Table: `signals`
Stores every alpha signal generated, regardless of whether it passed the risk gate.
```sql
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('LONG', 'SHORT', 'FLAT')),
    confidence_score DECIMAL(3,2) NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    alpha_metrics JSONB NOT NULL,
    risk_passed BOOLEAN NOT NULL,
    risk_reason TEXT,
    executed BOOLEAN DEFAULT FALSE,
    trade_id UUID REFERENCES trades(id)
);

CREATE INDEX idx_signals_timestamp ON signals(timestamp DESC);
CREATE INDEX idx_signals_direction_risk ON signals(direction, risk_passed);
```

### Table: `system_events`
Stores Watchdog alerts, proxy drops, state reconciliations, and errors.
```sql
CREATE TABLE system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    details JSONB NOT NULL
);

CREATE INDEX idx_system_events_severity ON system_events(severity, timestamp DESC);
```

---

## 4. Pydantic Schemas (Internal Data Flow)
These models define the exact Python objects passed between components. They use `pydantic v2` syntax and enforce strict typing.

### `GlobalState` (In-Memory / Redis Cache)
```python
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime
from typing import Literal

class GlobalState(BaseModel):
    symbol: str
    global_vwap: Decimal
    global_skew: float = Field(..., ge=0.0, le=1.0)
    global_funding_avg: Decimal
    prices: dict[str, Decimal]
    volumes_24h: dict[str, Decimal]
    last_update_utc: datetime
    status: Literal["ACTIVE", "STALE", "DEGRADED"] = "ACTIVE"

    model_config = {"json_encoders": {Decimal: str}}
```

### `TradingSignal` (Alpha Bridge Output)
```python
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime
from typing import Literal
from uuid import UUID

class TradingSignal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    alpha_metrics: dict[str, float | Decimal]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### `RiskDecision` (Risk Gate Output)
```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class RiskDecision(BaseModel):
    signal_id: UUID
    passed: bool
    gates_evaluated: list[str]
    reason_if_failed: Optional[str] = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### `TradeExecution` (Executor Output)
```python
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

class TradeExecution(BaseModel):
    trade_id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: Literal["BUY", "SELL"]
    size: Decimal
    entry_price: Decimal
    exit_price: Optional[Decimal] = None
    pnl_usdt: Optional[Decimal] = None
    execution_latency_ms: int
    status: Literal["PENDING", "FILLED", "PARTIAL", "CANCELLED", "FAILED"]
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None

    model_config = {"json_encoders": {Decimal: str}}
```

### Pydantic Models — 6-Stage Lifecycle

```python
class UniverseCandidate(BaseModel):
    """One symbol's score from Universe Scorer (Stage 1)."""
    symbol: str = Field(..., description="CCXT format, e.g. 'BTC/USDT'")
    volume_score: Decimal = Field(..., description="Aggregate 24h volume score (0-30)")
    momentum_score: Decimal = Field(..., description="1H price change % score (0-40)")
    overextension_penalty: Decimal = Field(..., description="Penalty for >30% 24h moves (-40 to 0)")
    squeeze_score: Decimal = Field(..., description="BB width narrowing score (0-30)")
    total_score: Decimal = Field(..., description="Sum of all components")
    sector: str = Field(..., description="Sector from sector_mapping.py")

class AnalystResult(BaseModel):
    """AI CryptoAnalyst output (Stage 3, mandatory)."""
    direction: Literal["LONG", "SHORT", "FLAT"] = Field(...)
    ai_confidence: Decimal = Field(..., ge=0, le=100, description="AI confidence 0-100")
    reasoning: str = Field(..., description="AI reasoning text")
    model_used: str = Field(..., description="Model ID, e.g. 'claude-haiku-3-5'")
    latency_ms: int = Field(..., description="9router call latency in ms")
    cached: bool = Field(False, description="Whether result came from Redis cache")

class JudgeVerdict(BaseModel):
    """AI Position Judge output (Stage 6, mandatory in ambiguous zone)."""
    action: Literal["HOLD", "EXIT", "TIGHTEN_STOP"] = Field(...)
    confidence: Decimal = Field(..., ge=0, le=100)
    reasoning: str = Field(...)
    tier: Literal["cheap", "escalated"] = Field(..., description="Which tier produced verdict")
    model_used: str = Field(...)
    consecutive_holds: int = Field(0, description="Hold streak count for this position")

class MultiTFResult(BaseModel):
    """Multi-timeframe confirmation output."""
    direction_agrees: bool = Field(..., description="4H trend agrees with 1H signal")
    ema_4h: Decimal = Field(..., description="EMA(20) on 4H candles")
    penalty_applied: Decimal = Field(Decimal("1.0"), description="0.5 if contradicts, 1.0 if agrees")
    data_available: bool = Field(True, description="False if 4H OHLCV unavailable")

class TradeMemoryEntry(BaseModel):
    """One historical trade stored for AI context injection."""
    symbol: str
    pnl_pct: Decimal = Field(..., description="Trade PnL as percentage")
    hold_duration_min: int = Field(..., description="Hold duration in minutes")
    regime: str = Field(..., description="Regime at time of trade")
    exit_reason: str = Field(..., description="trailing_stop/hard_fail/checkpoint/time_stop/ai_exit")
    entry_confidence: Decimal = Field(..., description="Signal confidence at entry")
    timestamp: float = Field(..., description="UNIX timestamp of trade close")
```

---

## 5. Data Flow Mapping (Component to Model)

| Component | Input Model | Output Model | Storage Target |
| :--- | :--- | :--- | :--- |
| **Universe Scorer** | `GlobalState` (all symbols) | `UniverseCandidate[]` | Redis (`system:universe:symbols`) |
| **Regime Engine** | OHLCV candles (BTC 1H) | Regime string | Redis (`system:config:regime`) |
| **CCXT Manager** | Raw WebSocket Dict | `GlobalState` | Redis (`global:state:{symbol}`) |
| **Alpha Bridge** | `GlobalState` + Regime | `TradingSignal` | Postgres (`signals`) |
| **Multi-TF Filter** | 4H OHLCV | `MultiTFResult` | In-memory (applied to signal) |
| **AI CryptoAnalyst** | `TradingSignal` + TA + Memory | `AnalystResult` | Redis cache (`ai:cache:*`) |
| **Risk Gate** | `TradingSignal` + `GlobalState` | `RiskDecision` | Postgres (`signals.risk_passed`) |
| **Sector Cap** | `PositionStore.list_all()` | accept/reject | In-memory check |
| **Bybit Executor** | `TradingSignal` + `RiskDecision` | `TradeExecution` | Postgres (`trades`) |
| **Trailing Stop** | Price + Position state | Amended SL | Exchange (via BybitClient) |
| **Checkpoint Manager** | Position PnL + ATR | Exit/Tighten/HOLD | Postgres (`trades`) |
| **AI Position Judge** | Position metadata + TA | `JudgeVerdict` | In-memory (actioned by checkpoint) |
| **Trade Memory** | `TradeExecution` (on exit) | `TradeMemoryEntry` | Redis (`karsa:memory:{symbol}`) |
| **Watchdog** | System Events / Metrics | `system_events` Dict | Postgres (`system_events`) |

---

## 6. State Reconciliation Mapping
On startup, the `State Manager` must map Bybit REST API responses to internal models:

| Bybit REST Response Field | Internal Model Field | Transformation Rule |
| :--- | :--- | :--- |
| `result.list[].symbol` | `TradeExecution.symbol` | Append `/USDT:USDT` if missing |
| `result.list[].side` | `TradeExecution.side` | Map `"Buy"` → `"BUY"`, `"Sell"` → `"SELL"` |
| `result.list[].size` | `TradeExecution.size` | Parse string to `Decimal` |
| `result.list[].avgPrice` | `TradeExecution.entry_price` | Parse string to `Decimal` |
| `result.list[].orderId` | `TradeExecution.exchange_order_id` | Direct string mapping |

---

## 7. Telegram Bot Layer (Key 7) Redis Keys

These keys are read and written by `app/bot/handlers.py`. They use `decode_responses=True` — all values are plain strings.

| Key | Type | TTL | Value Format | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `karsa:global_halt` | String | None | `"1"` = halt active | Emergency halt flag; set by kill handler, cleared by `/clear_halt` |
| `karsa:alerts_enabled` | String | None | `"1"` / `"0"` | Trade alert notifications toggle (default: on) |
| `karsa:auto:state:active` | String | None | `"1"` / `"0"` | Autonomous Session Manager active flag |
| `karsa:auto:config` | String (JSON) | None | `{"risk_pct": 30, "max_pos": 3, "interval_min": 15, "duration_min": 0}` | Active session parameters |
| `karsa:auto:start_time` | String | None | UNIX timestamp as float string | Session start time for next-scan countdown |
| `karsa:auto:pending_duration_min` | String | None | Integer minutes as string | Duration selected before risk level chosen; deleted after consumption |
| `karsa:crypto_cooldown` | String | 900s | `"1"` | 15-minute post-sell-all trading cooldown |
| `karsa:settings:max_positions` | String | None | `"3"` / `"5"` / `"8"` | Max open positions preference (cycles on toggle) |
| `karsa:settings:regime_filter` | String | None | `"1"` (enabled) / `"0"` (disabled) | Regime filter preference |
| `karsa:state:risk_profile` | String | None | `"conservative"` / `"semi_aggressive"` / `"aggressive"` | Active risk profile name |

> **Note:** The `karsa:auto:*` keys are written by the Autonomous Session Manager (ASM). The ASM is not yet fully ported — these keys are documented here so handlers can read them safely once ASM is available.

> **Open Issue:** Redis scope is an open conflict (see `CONTEXT.md` §7, Issue #1). The presence of `RedisClient` in `app/core/` and these keys in the data model means Redis is treated as IN SCOPE. This should be formally resolved and the conflict closed.