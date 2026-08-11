# Data Model & Schema Dictionary
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Live Specification  
**Last Revised:** 2026-08-11

---

## 1. Core Serialization & Precision Rules

1. **Strict `decimal.Decimal` for Money**: All financial values (prices, quantities, costs, PnL, thresholds) MUST use Python `decimal.Decimal`. Raw floats for money are banned across the codebase.
2. **JSON & Redis String Serialization**: Serialize `Decimal` objects as formatted string representations (e.g. `"64250.50"`) when converting to JSON/Redis, and parse back using `Decimal("64250.50")` on deserialization.
3. **UTC Timestamps**: All timestamps use explicit UTC (`datetime.now(timezone.utc)`). Stored as ISO 8601 strings in JSON/Redis and `TIMESTAMPTZ` in PostgreSQL.
4. **Pydantic Model Validation**: Field shapes and variable names must strictly match the definitions in `app/` dataclasses and Pydantic models.

---

## 2. Redis Key Namespace Dictionary

Redis serves as the real-time cache and shared state transport across containers.

| Key Pattern | Type | TTL | Payload Structure (JSON) | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `onchain:symbol:{symbol}` | String | 30s | `{"symbol": "ETH/USDT", "dex_price": 2740.50, "updated_at": 1723370000}` | EVM slot0 DEX price for CEX vs DEX divergence signal |
| `karsa:wallet:latest` | String | 120s | `{"balance": 105.789, "available": 89.859, "ok": true}` | Wallet balance cache written by live wallet loop |
| `karsa:global_halt` | String | None | `"1"` or `""` | Global trading suspension toggle |
| `karsa:auto:state:active` | String | None | `"1"` or `"0"` | Session active status |
| `system:config:regime` | String | None | `{"regime": "TREND_BULL", "hurst": 0.58, "adx": 28.5, "atr_pct": 12.0}` | RegimeClassifier output |
| `system:universe:symbols` | String | None | `{"symbols": ["BTC/USDT", ...], "scores": {"BTC/USDT": 82.5}}` | Dynamic universe scorer active list |
| `karsa:position:{symbol}:{side}` | Hash | None | `{entry_price, peak_price, sl_order_id, risk_profile, ...}` | Live open position state |
| `shadow:position:{symbol}:{side}` | String | None | `{entry_price, virtual_sl, virtual_tp, worst_price_seen, status, ...}` | Shadow simulation position state |
| `karsa:sector:{sector_name}` | String | None | `"1"` | Active open position counter per sector |
| `karsa:memory:{symbol}` | ZSet | None | score=UNIX timestamp, member=JSON trade summary | Historical trade memory for AI context |
| `risk:portfolio_cb:daily_loss_fired` | String | None | `"0"` or `"1"` | Portfolio daily loss CircuitBreaker trigger state |
| `risk:portfolio_cb:consecutive_loss_count` | String | None | `"2"` | Current consecutive loss count |
| `risk:portfolio_cb:start_of_day_equity` | String | None | `"105.79"` | Midnight UTC equity baseline snapshot |
| `karsa:features:{symbol}` | String | 3600s | `{"rsi_14": 48.2, "bb_width": 0.035, "macd_hist": 12.4}` | Calculated technical features cache |

---

## 3. Pydantic & Data Transfer Objects (DTOs)

### A. `TradeSignal` (`app/alpha/signals.py`)
```python
class TradeSignal(BaseModel):
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    confidence: Decimal  # Normalized 0.00 to 1.00
    regime: str  # TREND_BULL | TREND_BEAR | RANGE | CHOP
    strategy: str  # TREND_MOMENTUM | MEAN_REVERSION | CHOP_SWEEP
    raw_ev: Decimal  # Calculated EV score
    threshold: Decimal  # Effective dynamic EV threshold
    metadata: dict[str, Any] = {}
    timestamp: datetime
```

### B. `DecisionContext` (`app/core/decision_context.py`)
```python
@dataclass
class DecisionContext:
    decision_id: str
    symbol: str
    timeframe: str
    regime: str
    signal_direction: str
    quant_confidence: float
    ev_score: float
    ev_threshold: float
    ai_approved: bool
    ai_confidence: float
    risk_passed: bool
    rejection_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

### C. `Position` (`app/core/state.py`)
```python
class Position(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry_price: Decimal
    amount: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None = None
    sl_order_id: str | None = None
    tp_order_id: str | None = None
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    created_at: datetime
    updated_at: datetime
```

---

## 4. PostgreSQL Schema (Audit & Historical Persistence)

### Table `trades`
```sql
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(5) NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    size DECIMAL(20,8) NOT NULL,
    entry_price DECIMAL(20,8) NOT NULL,
    exit_price DECIMAL(20,8),
    pnl_usdt DECIMAL(20,8),
    execution_latency_ms INTEGER,
    status VARCHAR(10) NOT NULL DEFAULT 'FILLED',
    exit_reason VARCHAR(50),
    order_id VARCHAR(100),
    exchange_order_id VARCHAR(100),
    risk_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    global_state_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_timestamp ON trades(timestamp);
```

### Table `decisions`
```sql
CREATE TABLE decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(5) NOT NULL,
    ev_score DECIMAL(10,4) NOT NULL,
    ev_threshold DECIMAL(10,4) NOT NULL,
    quant_confidence DECIMAL(10,4) NOT NULL,
    ai_confidence DECIMAL(10,4),
    ai_approved BOOLEAN NOT NULL DEFAULT FALSE,
    risk_passed BOOLEAN NOT NULL DEFAULT FALSE,
    executed BOOLEAN NOT NULL DEFAULT FALSE,
    rejection_reason TEXT,
    context_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_decisions_symbol ON decisions(symbol);
CREATE INDEX idx_decisions_timestamp ON decisions(timestamp);
```

### Table `shadow_trades`
```sql
CREATE TABLE shadow_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(5) NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    size DECIMAL(20,8) NOT NULL,
    entry_price DECIMAL(20,8) NOT NULL,
    exit_price DECIMAL(20,8) NOT NULL,
    pnl_usdt DECIMAL(20,8) NOT NULL,
    exit_reason VARCHAR(50) NOT NULL,
    regime VARCHAR(20) NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    virtual_fees DECIMAL(20,8) NOT NULL DEFAULT 0.00000000
);
CREATE INDEX idx_shadow_trades_symbol ON shadow_trades(symbol);
```