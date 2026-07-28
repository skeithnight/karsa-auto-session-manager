# KASM — Complete Codebase Reference
**Project:** `karsa-auto-session-manager`
**Last Updated:** 2026-07-24
**Purpose:** Single-source document describing the entire system — architecture, tech stack, infrastructure, modules, data flow, and operations.

> For working rules (AI agents), see `AGENTS.md`. For orientation and open issues, see `CONTEXT.md`. For source-of-truth order, see `AGENTS.md §1`.

---

## 1. Overview

### What Is KASM?
An autonomous crypto perpetuals trading bot that **reads market data from multiple exchanges** (Binance, OKX, Bybit) to build a "true" global price picture, but **only ever trades on Bybit** — because Bybit requires a WireGuard VPN proxy due to geo-restrictions. The strategy targets **15-minute to 4-hour swing/intraday structure** so that proxy latency (~100-300ms) is noise relative to the holding period.

### Core Thesis: "Read Global, Execute Local"
```
┌──────────────────────────────────────────────────────────┐
│                  THE READ PIPELINE                        │
│                                                          │
│  Binance WS ──┐                                          │
│  OKX WS ──────┼──→ CCXT Pro ──→ Normalizer ──→ GlobalState│
│  Bybit WS ────┘                                          │
│                                                          │
│  "See the true market state across all venues"           │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│                  THE WRITE PIPELINE                       │
│                                                          │
│  GlobalState ──→ Alpha ──→ Risk ──→ SOR ──→ Bybit Only   │
│                                                          │
│  "Execute only on Bybit, using global insight as edge"   │
└──────────────────────────────────────────────────────────┘
```

### Why Single-Process Monolith?
| Decision | Rationale | Rejected Alternative |
|:---|:---|:---|
| Single asyncio process | Proxy adds ~150ms; internal IPC would compound latency and risk state divergence | Microservices via Redis Pub/Sub |
| 15m-4h timeframe | Proxy latency is mathematically irrelevant at this horizon | HFT millisecond scalping |
| Bybit-only execution | Avoids cross-exchange arbitrage complexity and multi-venue proxy overhead | Multi-exchange execution |
| `Decimal` everywhere | Float precision loss is unacceptable for PnL calculations | `float` |
| Exchange-side SL on every fill | Bot's in-memory SL is worthless if process dies | Relying on bot-managed SL only |

### Current Phase Status
| Phase | Name | Status |
|:---|:---|:---|
| 1 | 5-Container Fleet | **0%** — single-process monolith |
| 2 | Core Trading Engine | **~90%** — all major engines built |
| 3.1 | Shadow Mode | **100%** — all 4 refinements applied |
| 3.2 | Backtest Worker | **0%** — no code yet |
| 4 | Commander & Telegram | **0%** |
| 5 | Telemetry & Observability | **~40%** — 16 Prometheus metrics exist |
| 6 | Go-Live Protocol | N/A — process doc |

---

## 2. Tech Stack

| Component | Technology | Version | Purpose |
|:---|:---|:---|:---|
| **Language** | Python | 3.11+ | Core runtime |
| **Concurrency** | asyncio | — | Single-process event loop for WS + DB |
| **Market Data** | ccxt.pro | ≥4.0.0 | Unified WebSocket API across exchanges |
| **Exchange Client** | pybit | ≥5.7.0 | Bybit REST/WS (private) |
| **Database** | PostgreSQL | 16 | Trade logs, signals, state persistence |
| **DB Driver** | asyncpg | ≥0.29.0 | Fastest async Python Postgres driver |
| **Cache** | Redis | 7 | High-speed state, heartbeats, positions |
| **AI Proxy** | 9router | latest | OpenAI-compatible LLM proxy (Claude models) |
| **ML/XGBoost** | xgboost | ≥2.0.3 | ML signal pre-filter |
| **HMM** | hmmlearn | ≥0.3.0 | Hidden Markov Model regime detection |
| **GARCH** | arch | ≥7.0.0 | Volatility forecasting for Kelly sizing |
| **TA** | ta | ≥0.10.0 | Technical analysis indicators |
| **Data** | pandas | ≥2.1.4 | Data manipulation |
| **Stats** | scipy | ≥1.11.0 | Statistical functions |
| **ML** | scikit-learn | ≥1.3.2 | ML utilities |
| **HTTP** | aiohttp | ≥3.9.0 | Async HTTP client (9router calls) |
| **Telegram** | python-telegram-bot | ≥20.0 | Bot interface, alerts, kill switch |
| **Monitoring** | prometheus-client | ≥0.19.0 | Metrics exposition |
| **Logging** | loguru | ≥0.7.0 | Structured logging |
| **Migrations** | alembic | ≥1.13.0 | Database schema management |
| **ORM** | sqlalchemy | ≥2.0.0 | Database abstraction |
| **Proxy** | WireGuard VPN | — | Geo-restriction bypass (via gluetun) |
| **Container** | Docker + Compose | — | Deployment and isolation |
| **Linting** | ruff + black + mypy | — | Code quality enforcement |
| **Testing** | pytest + hypothesis | — | Unit/integration testing |

---

## 3. Infrastructure

### Docker Architecture (Split Stack)

The system uses a **two-file Docker Compose split** to prevent app rebuilds from cascading into infrastructure recreation (which would kill 9router state, drop VPN tunnels, etc.).

```
┌─────────────────────────────────────────────────────────────────┐
│  docker-compose.infra.yml (rarely changes)                      │
│                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌───────┐ │
│  │ Postgres │ │  Redis   │ │ Gluetun  │ │9router  │ │Prom/G │ │
│  │ :5432    │ │ :6379    │ │ (VPN)    │ │(AI prox)│ │rafana │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ └───────┘ │
│                                                                 │
│  Network: karsa-backend (172.28.0.0/16)                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  docker-compose.apps.yml (rebuild frequently)                   │
│                                                                 │
│  ┌──────────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐  │
│  │ karsa-data-  │ │karsa-live│ │karsa-     │ │karsa-commander│  │
│  │ engine       │ │          │ │shadow     │ │              │  │
│  │ :8003       │ │ :8001    │ │ :8002     │ │ :8004        │  │
│  └──────────────┘ └──────────┘ └───────────┘ └──────────────┘  │
│                                                                 │
│  All route through gluetun VPN (network_mode: container:...)   │
└─────────────────────────────────────────────────────────────────┘
```

### Container Details

| Container | Role | Network | Port |
|:---|:---|:---|:---|
| `karsa-postgres` | PostgreSQL 16 | karsa-backend | 5432 |
| `karsa-redis` | Redis 7 (AOF, 256MB max) | karsa-backend | 6379 |
| `karsa-gluetun` | WireGuard VPN tunnel | karsa-backend | 20129 (mapped) |
| `karsa-9router` | AI LLM proxy (Claude) | via gluetun | — |
| `karsa-prometheus` | Metrics scraping | karsa-backend | 9090 |
| `karsa-grafana` | Dashboards | both | 3000 |
| `karsa-data-engine` | Standalone data ingestion | via gluetun | 8003 |
| `karsa-live` | Live trading (SHADOW_MODE=false) | via gluetun | 8001 |
| `karsa-shadow` | Shadow trading (SHADOW_MODE=true) | via gluetun | 8002 |
| `karsa-backtest` | Backtesting (planned) | karsa-backend | 8005 |
| `karsa-commander` | CLI management | via gluetun | 8004 |

### VPN / Proxy Architecture

```
Bybit API (geo-blocked)
    │
    ▲
    │ WireGuard UDP (port 51820)
    │
┌───┴──────────────────┐
│  DigitalOcean Droplet │  ← Sydney, Australia
│  WireGuard Server     │
└──────────────────────┘
    │
    ▲
    │ WireGuard Tunnel
    │
┌───┴──────────────────┐
│  karsa-gluetun        │  ← Docker sidecar, NET_ADMIN
│  (gluetun image)      │
│  127.0.0.1 → 1.1.1.1  │  ← DNS override
└──────────────────────┘
    │
    ├── karsa-live       (network_mode: container:karsa-gluetun)
    ├── karsa-shadow     (network_mode: container:karsa-gluetun)
    ├── karsa-data-engine(network_mode: container:karsa-gluetun)
    ├── karsa-commander  (network_mode: container:karsa-gluetun)
    └── karsa-9router    (network_mode: service:gluetun)
```

### Makefile Commands

| Command | What It Does |
|:---|:---|
| `make up` | Cold start — both infra + apps stacks |
| `make rebuild` | Rebuild apps only (infra untouched) |
| `make down` | Stop everything (preserves volumes) |
| `make restart-apps` | Restart apps without rebuild |
| `make logs` | Tail app logs |
| `make logs-infra` | Tail infra logs |
| `make cleanup` | Prune Docker disk usage |
| `make disk-check` | Alert if disk > 80% |
| `make db-maintenance` | Daily DB backup + cleanup |
| `make db-backup` | Backup DB only |

### Volumes

| Volume | Purpose |
|:---|:---|
| `postgres_data` | PostgreSQL persistent storage |
| `redis_data` | Redis AOF persistence |
| `grafana_data` | Grafana dashboards/settings |
| `9router_data` | 9router AI proxy state |
| `gluetun_data` | VPN configuration |

---

## 4. Architecture — The 7 Keys

The system's responsibilities are divided into 7 "Keys" — logical domains that map to code directories. They are not separate processes; everything runs in a single `asyncio` loop.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SINGLE PYTHON PROCESS (asyncio)                   │
│                                                                     │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐           │
│  │ Key 1       │    │ Key 2        │    │ Key 3        │           │
│  │ Global Data │───▶│ Alpha Bridge │───▶│ Risk Gate    │           │
│  │ Engine      │    │ (Hub+Spokes) │    │              │           │
│  │ app/data/   │    │ app/alpha/   │    │ app/risk/    │           │
│  └─────────────┘    └──────────────┘    └──────────────┘           │
│         │                  │                    │                    │
│         │                  │                    ▼                    │
│         │                  │            ┌──────────────┐           │
│         │                  │            │ Key 4        │           │
│         │                  │            │ Bybit Exec + │           │
│         │                  │            │ APM          │           │
│         │                  │            │ app/execution│           │
│         │                  │            └──────────────┘           │
│         │                  │                    │                    │
│         ▼                  ▼                    ▼                    │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐           │
│  │ Key 5       │    │ Key 6        │    │ Key 7        │           │
│  │ State Mgr   │    │ Watchdog &   │    │ Telegram Bot │           │
│  │ Postgres    │    │ Telemetry    │    │ Alerts/Cmds  │           │
│  │ app/core/   │    │ app/watchdog/│    │ app/bot/     │           │
│  └─────────────┘    └──────────────┘    └──────────────┘           │
└─────────────────────────────────────────────────────────────────────┘
```

### Key 1 — Global Data Engine (`app/data/`)
**Mission:** Ingest and normalize market data from multiple exchanges into a unified view.

| Module | Purpose |
|:---|:---|
| `ccxt_manager.py` | CCXT Pro WebSocket connections, `load_markets()` validation |
| `normalizer.py` | ONLY place raw exchange dicts are touched directly |
| `filters.py` | Bad tick rejection (price spikes >5% in <1s) |
| `ohlcv_fetcher.py` | Cached OHLCV REST fetcher (TTL-based) |
| `universe_scorer.py` | Dynamic symbol scoring (Volume+Momentum+Squeeze+Overextension) |
| `universe_scanner.py` | Periodic universe re-scan (new listings, delistings) |
| `market_data_ingestor.py` | Historical data ingestion, volatility floor calculation |
| `sector_mapping.py` | Static sector classification (BTC/ETH, L1, L2, DeFi, Meme, AI, RWA) |

### Key 2 — Alpha Bridge (`app/alpha/`)
**Mission:** Classify market regime and generate calibrated confidence scores for entries.

The Alpha Bridge operates as a **Hub-and-Spoke** system:

```
                    ┌─────────────────────┐
                    │   RegimeClassifier   │
                    │   (The Hub)          │
                    │   ADX + Hurst + ATR  │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
     │ TREND_BULL/ │ │   RANGE     │ │    CHOP     │
     │ TREND_BEAR  │ │             │ │  (no trade) │
     └──────┬──────┘ └──────┬──────┘ └─────────────┘
            ▼               ▼
     ┌─────────────┐ ┌─────────────┐
     │ Momentum +  │ │ BB Edge-    │
     │ Breakout +  │ │ Fade + Wick │
     │ Global Sync │ │ Rejection   │
     └──────┬──────┘ └──────┬──────┘
            ▼               ▼
     ┌─────────────────────────────┐
     │    StrategyRouter (Spokes)  │
     │    Regime-specific scoring  │
     │    Output: 0-100 confidence │
     └─────────────┬───────────────┘
                   │
                   ▼
     ┌─────────────────────────────┐
     │    EntryFilter (5 checks)   │
     │    MultiTF (4H + Macro)     │
     │    CryptoAnalyst (AI)       │
     └─────────────────────────────┘
```

| Module | Purpose |
|:---|:---|
| `regime_classifier.py` | **The Hub** — ADX + Hurst + ATR → TREND_BULL/BEAR, RANGE, CHOP |
| `strategy_router.py` | **The Spokes** — regime-specific confidence scoring |
| `signals.py` | Multi-signal composite (skew+lead_lag+funding+OI) |
| `entry_filter.py` | Pre-entry structural checklist (5 checks) |
| `ta_tools.py` | Deterministic TA indicators (RSI, BB, MACD, ATR, EMA) |
| `analyst.py` | **MANDATORY** AI pre-entry review via 9router |
| `position_judge.py` | **MANDATORY** AI post-entry assessment (2-tier) |
| `multi_tf.py` | 4H trend confirmation + macro anchor penalty |
| `lead_lag_buffer.py` | 15-minute rolling price buffer (Binance vs Bybit) |
| `trade_memory.py` | Historical trade context injection for AI prompts |
| `ml_prefilter.py` | ML-based signal pre-filter |
| `market_analyzer.py` | Market structure analysis |
| `market_state.py` | Market state dataclass |
| `macro_narrator.py` | 4-hour AI macro state assessment (RISK_ON/RISK_OFF/CHOP) |

### Key 3 — Risk Gate (`app/risk/`)
**Mission:** Prevent any single trade or sequence from causing unrecoverable account damage.

**Execution order (mandatory):**
```
PortfolioRiskManager.check()     ← runs first
    ↓ passes
gates.check()                    ← 3-layer gate
    ↓ passes
sector_cap.check()               ← max 2 per sector
    ↓ passes
CircuitBreaker check             ← daily loss + consecutive losses
    ↓ passes
BybitExecutor.execute()          ← never call without passing all above
```

| Module | Purpose |
|:---|:---|
| `portfolio_risk_manager.py` | Pre-trade: correlation trap, gross/net exposure, CB state |
| `gates.py` | 3-layer: liquidity, spread health, circuit breaker |
| `circuit_breaker.py` | Per-session -2% drawdown hard stop |
| `sector_cap.py` | Max 2 positions per sector |
| `dynamic_risk_gate.py` | Regime-specific risk profiles (spread, sizing) |
| `garch_volatility_forecaster.py` | GARCH volatility targeting for Kelly sizing |
| `kelly_sizer.py` | Kelly criterion position sizing with GARCH adjustment |

### Key 4 — Bybit Executor + APM (`app/execution/`)
**Mission:** Fill orders with optimal market impact and defend every open position until closure.

| Module | Purpose |
|:---|:---|
| `bybit_client.py` | Bybit REST/WS client, exchange-side SL/TP placement |
| `sor.py` | Smart Order Router: Post-Only → Reprice → Market/IOC |
| `position_manager.py` | **ActivePositionManager** — 2s async loop, breakeven, trailing, regime shift kill |
| `position_lifecycle.py` | TrailingStopManager + CheckpointManager |
| `shadow.py` | ShadowExecutor + ShadowAPM + ShadowExchangeClient |

### Key 5 — State Manager (`app/core/`)
**Mission:** Persist all trade events, risk decisions, and state changes.

| Module | Purpose |
|:---|:---|
| `config.py` | Pydantic Settings, loads `.env` — secrets live ONLY here |
| `database.py` | PostgreSQL async pool (asyncpg) |
| `redis_client.py` | Redis async client |
| `state.py` | In-memory state + Postgres sync |
| `position_store.py` | Redis-backed position lifecycle state |
| `shadow_store.py` | ShadowPositionStore + ShadowTradeStore |
| `trade_store.py` | Postgres trade CRUD |
| `ai_client.py` | 9router async HTTP client |
| `metrics.py` | Prometheus metrics (26+ counters, gauges, histograms) |
| `telemetry.py` | System telemetry collection |
| `decision_context.py` | Decision context dataclass for pipeline |
| `decision_trace.py` | Decision tracing for audit trail |
| `feature_registry.py` | Feature registry for ML models |
| `feature_store.py` | Feature store for ML inference |

### Key 6 — Watchdog & Telemetry (`app/watchdog/`)
**Mission:** Detect when the system is "going blind" or "going rogue" and take defensive action.

| Module | Purpose |
|:---|:---|
| `monitor.py` | WS heartbeat monitor, execution latency tracker, event loop lag |
| `dead_mans_switch.py` | External health ping (Healthchecks.io) |
| `system_watchdog.py` | System watchdog with health checks |
| `system_doctor.py` | AI-driven diagnostic agent (triggered on circuit breaker) |

### Key 7 — Telegram Bot (`app/bot/`)
**Mission:** Command interface, alerts, kill switch.

| Module | Purpose |
|:---|:---|
| `handlers.py` | All command & callback handlers |
| `runner.py` | PTB app builder, bot_data wiring, startup |
| `alert_service.py` | Telegram alert sender |
| `formatters/` | Trade history, live/shadow funnel formatters |

---

## 5. End-to-End Trade Lifecycle (6 Stages)

### Stage 1 — Universe Selection
**File:** `app/data/universe_scorer.py`
**Frequency:** Every 4 hours

Scores all configured symbols 0–100:
- **Volume** (0–30): Aggregate 24h volume across Binance+OKX+Bybit
- **Momentum** (0–40): 1H price change %
- **Overextension** penalty (-40 to -10): Penalize >30% 24h moves
- **Squeeze** (0–30): BB width narrowing on 1H

Output: Top 15 symbols above score 55, respecting sector cap. Shared via Redis `system:universe:symbols`.

### Stage 2 — Regime Detection
**File:** `app/alpha/regime_classifier.py`
**Frequency:** Every 15 minutes on BTC/USDT 1H (200 bars)

Three indicators:
- **Hurst Exponent** (R/S method, windows 10/20/40): H > 0.55 = trending, H < 0.45 = mean-reverting
- **ADX(14)**: > 25 = strong trend, < 20 = choppy
- **ATR percentile**: Volatility context

Output: `TREND_BULL`, `TREND_BEAR`, `RANGE`, or `CHOP`. **CHOP halts all signal generation.**

### Stage 3 — Signal Generation (AI-Mandatory)
**File:** `app/consumer/decision_engine.py`

Sequential pipeline:
1. **StrategyRouter** — Regime-specific confidence scoring (0–100)
2. **EntryFilter** — 5 checks: regime, spread, depth, time-of-day, duplicate
3. **MultiTFFilter** — 4H EMA(20) trend check (0.5x penalty if fighting) + Macro Anchor (0.8x penalty)
4. **CryptoAnalyst** (MANDATORY) — 200 1H candles → TA → AI prompt → confidence blend: `quant × 0.5 + ai × 0.5`. Gate: >= 0.65. **AI failure → 0 confidence → REJECT**

### Stage 4 — Risk Gate
**File:** `app/risk/portfolio_risk_manager.py` → `app/risk/gates.py` → `app/risk/sector_cap.py`

Sequential:
1. **PortfolioRiskManager** — Correlation trap, gross/net exposure, CB state
2. **RiskGate** — Liquidity (24h vol ≥ $1M), spread (≤ 0.5%), circuit breaker
3. **SectorCap** — Max 2 per sector
4. **CircuitBreaker** — Daily loss ≥ 2% → HALT; 3 consecutive losses → SOFT STOP

### Stage 5 — SOR Execution
**File:** `app/execution/sor.py`

1. **Iceberg Slicing** — If notional > $2,000: 4 hidden chunks, randomized 1.5–3.5s delays
2. **Post-Only Limit** at current price
3. **Adaptive Reprice** — Up to 2 attempts; if spread widens > 0.2%, drop delay to 100ms
4. **Market/IOC fallback**
5. **Exchange-side SL placed IMMEDIATELY on fill** — via `bybit_client.place_stop_loss()`

### Stage 6 — Post-Entry Management (APM)
**File:** `app/execution/position_manager.py`
**Loop:** Every 2 seconds

| Feature | Description |
|:---|:---|
| **R-Multiple Tracking** | `(live_price - entry) / initial_risk_per_unit` |
| **Breakeven Lock** | At +0.25R: move SL to entry + fees (free roll) |
| **Asymmetric Time Exits** | Losers: 3min kill. Breakeven: 15min. Winners: no limit |
| **Regime Shift Kill** | If regime changes after entry → close at market |
| **Trailing Stop** | 3x ATR Chandelier for TREND (activates at +1.5R) |
| **CheckpointManager** | Every 5min: HARD_FAIL, CLEAR_WIN, AMBIGUOUS→AI Judge, TIME_STOP |
| **AI Position Judge** | 2-tier (haiku→sonnet). 3 HOLDs on loser → forced EXIT |
| **Momentum Decay** | If winning but R stalls 10min and drops below 80% of peak → exit |

### Async Task Architecture

```
main()
  │
  ├── data_engine_task()              ← CCXT WS, normalizer, Redis writes
  │
  ├── regime_engine_task()            ← RegimeClassifier, every 15min
  │   └── macro_narrator._assess()   ← AI macro state, every 4h
  │
  ├── alpha_bridge_task()             ← Signal → EntryFilter → MultiTF → AI
  │   └── outputs to signal_queue
  │
  ├── risk_gate_task()                ← PRM → Gate → SectorCap → CircuitBreaker
  │   └── outputs to risk_queue
  │
  ├── executor_task()                 ← SOR → Fill → SL placement → register
  │
  ├── active_position_manager()       ← 2s loop, breakeven, trailing, regime shift
  │
  ├── watchdog_task()                 ← Heartbeats, latency, dead man's switch
  │
  ├── position_reconciler_task()      ← Every 5min, internal vs Bybit REST
  │
  ├── metrics_publisher_task()        ← Prometheus /metrics endpoint
  │
  └── trade_reconciler_task()         ← Postgres audit trail sync
```

---

## 6. Module Deep Dive

### `app/core/` — Configuration & Infrastructure

**`config.py`** — Pydantic Settings class loading from `.env`:
```python
# Key settings (partial):
class Settings(BaseSettings):
    # Exchange
    BYBIT_API_KEY: str
    BYBIT_API_SECRET: str
    
    # Database
    POSTGRES_URL: str
    REDIS_URL: str
    
    # AI
    AI_ROUTER_URL: str = "http://127.0.0.1:20129"
    
    # Shadow Mode
    SHADOW_MODE_ENABLED: bool = False
    
    # Risk
    CIRCUIT_BREAKER_DRAWDOWN: Decimal = Decimal("-0.02")
    
    # Session Block (Asian dead zone)
    SESSION_BLOCK_ENABLED: bool = True
    SESSION_BLOCK_START_HOUR: int = 4    # UTC
    SESSION_BLOCK_END_HOUR: int = 12     # UTC
    
    # Volatility Floor
    VOL_FLOOR_PERCENTILE: int = 25
    VOL_FLOOR_LOOKBACK_DAYS: int = 90
    
    # Macro Narrator
    MACRO_NARRATOR_INTERVAL_S: int = 14400  # 4 hours
```

**`database.py`** — Async PostgreSQL pool via `asyncpg`
**`redis_client.py`** — Async Redis client
**`ai_client.py`** — 9router HTTP client (OpenAI-compatible format)
**`metrics.py`** — 26+ Prometheus metrics covering all components

### `app/data/` — Data Ingestion

**`ccxt_manager.py`** — Manages CCXT Pro WebSocket connections:
- Binance, OKX, Bybit public feeds
- Auto-reconnection on drops
- `load_markets()` for symbol validation

**`normalizer.py`** — The ONLY place raw exchange dicts are touched:
- Converts exchange-specific schemas to unified internal format
- All other modules consume normalized data

**`filters.py`** — Bad tick rejection:
- Price spike >5% in <1s → filtered out
- Ensures data quality before downstream consumption

**`universe_scorer.py`** — Dynamic symbol selection:
- Scores 0-100 based on Volume + Momentum + Squeeze - Overextension
- Top N symbols above threshold
- Respects sector cap (max 2 per sector)

**`market_data_ingestor.py`** — Also handles:
- Historical candle ingestion
- Volatility floor calculation (90-day 25th percentile of BTC 1H ATR)
- GARCH ratio updates

### `app/alpha/` — Signal Generation

**`regime_classifier.py`** (The Hub):
- ADX(14) + Hurst Exponent + ATR percentile
- Outputs: TREND_BULL, TREND_BEAR, RANGE, CHOP
- BTC-only classification (applied to all symbols)

**`strategy_router.py`** (The Spokes):
- TREND: Momentum + breakout + global exchange sync (fakeout detection)
- RANGE: Bollinger Band edge-fade + wick rejection + RSI exhaustion
- CHOP: Orderbook liquidity sweep + funding rate extremes

**`ta_tools.py`** — Deterministic technical indicators:
- RSI, Bollinger Bands, MACD, ATR, EMA
- Pure math, no network calls
- Used for AI context and signal generation

**`analyst.py`** (MANDATORY AI):
- Fetches 200 1H candles
- Computes TA indicators
- Sends structured prompt to 9router (claude-haiku-3-5)
- Final confidence = quant × 0.5 + ai × 0.5
- Gate: >= 0.65
- **AI failure → returns 0 → REJECT (never bypass)**

**`position_judge.py`** (MANDATORY AI):
- 2-tier escalation: haiku (cheap) → sonnet (escalated)
- 3 consecutive HOLDs on losing position → forced EXIT
- Fail-safe: AI unavailable → HOLD (don't exit without AI)

**`macro_narrator.py`**:
- 4-hour cycle via 9router
- Classifies: RISK_ON / RISK_OFF / CHOP
- Multipliers: 1.0x / 0.25x / 0.5x
- Soft sizing multiplier, not a hard gate

### `app/risk/` — Risk Management

**`portfolio_risk_manager.py`** — Pre-trade gate:
1. Correlation trap: max 2 concurrent positions per sector
2. Gross exposure: total notional ≤ X% of equity
3. Net exposure: directional imbalance ≤ Y% of equity
4. Circuit breaker state: if fired today, block all entries

**`gates.py`** — 3-layer gate:
1. Circuit breaker: `is_halted()` / `is_paused()`
2. Liquidity: 24h volume ≥ $1M
3. Spread health: bid-ask ≤ 0.5%

**`circuit_breaker.py`** — Hard stop:
- Daily drawdown ≥ -2% → HALT all entries
- Per-session protection

**`garch_volatility_forecaster.py`**:
- GARCH(1,1) volatility forecasting
- Ratio of forecasted vs historical vol
- When ratio < 0.7: scale UP (1.2x) — vol compressing, breakout likely

**`kelly_sizer.py`**:
- Kelly criterion position sizing
- GARCH-adjusted for volatility targeting

### `app/execution/` — Order Execution

**`bybit_client.py`**:
- Bybit REST/WS client
- Exchange-side SL/TP placement (`place_stop_loss()`, `amend_stop_loss()`)
- Private WebSocket for order management

**`sor.py`** — Smart Order Router:
1. Iceberg slicing (>$2,000 notional)
2. Post-Only Limit order
3. Adaptive Reprice (up to 2 attempts)
4. Market/IOC fallback
5. **Exchange-side SL placed immediately on fill**

**`position_manager.py`** — ActivePositionManager:
- 2-second async monitoring loop
- R-Multiple tracking
- Breakeven lock at +0.25R
- Asymmetric time exits (losers: 3min, breakeven: 15min)
- Regime shift kill switch (3 consecutive mismatch checks)
- Trailing stop (3x ATR Chandelier for TREND)
- CheckpointManager (HARD_FAIL, CLEAR_WIN, AMBIGUOUS→AI, TIME_STOP)
- Momentum decay exit

### `app/consumer/` — Trading Loops

**`market_consumer.py`** — CCXT WebSocket consumer:
- Normalizes raw exchange data into GlobalState
- Handles WS reconnection gracefully

**`candle_buffer.py`** — Aggregates ticks into OHLCV candles per timeframe

**`decision_engine.py`** — Orchestrates the full pipeline:
- Volatility floor check (BTC ATR < threshold → BLOCK)
- Session hard-block (04:00-12:00 UTC for alts)
- Macro narrator sizing multiplier
- Strategy → Filter → MultiTF → AI → Risk → Execute

**`live_loop.py`** — Main live trading loop
**`shadow_loop.py`** — Shadow mode loop (same pipeline, simulated execution)

### `app/learning/` — Statistical Learning

**`expected_edge.py`** — Expected edge calculation for trade evaluation
**`similarityEngine.py`** — Trade similarity matching (finds comparable historical trades)

### `app/watchdog/` — Health Monitoring

| Check | Threshold | Action |
|:---|:---|:---|
| WS heartbeat | >10s no data | Pause Alpha Bridge |
| Execution latency | >1500ms avg | SOR skips to Market orders |
| Event loop lag | >100ms × 3 checks | Flatten all, shutdown |
| Dead man's switch | 60s ping | External alert if missed |
| Stale data | >15s no update | Halt new entries |

### `app/bot/` — Telegram Interface

- Commands gated by `_is_authorized()` (checks `TELEGRAM_CHAT_ID`)
- Accesses `BybitClient` and `RedisClient` via `bot_data` (no globals)
- Kill switch: PTB stops when global `kill_switch` Event is set
- Alert service for trade notifications

### `app/data_engine/` — Standalone Data Service

Runs as separate Docker container (routed through gluetun):
- `exchange_connector.py` — CCXT connection manager
- `postgres_cacher.py` — Persists OHLCV candles to Postgres
- `redis_publisher.py` — Publishes live ticks to Redis streams

### `app/backtest/` — Backtesting (Phase 3.2, Not Yet Built)

- `engine.py` — Core backtest runner
- `orchestrator.py` — Multi-symbol/strategy coordinator
- `worker.py` — Parallel execution worker
- `formatter.py` — Results formatting
- `optimizer.py` — Walk-forward parameter optimization

---

## 7. Data Model

### Serialization Rules (CRITICAL)
1. **Never use `float` for money.** Always `decimal.Decimal`.
2. **JSON/Redis:** Serialize `Decimal` as strings. Parse back on read.
3. **Timestamps:** Always UTC. ISO 8601 in Redis/JSON, `TIMESTAMPTZ` in Postgres.
4. **Immutability:** Pydantic models treated as immutable.

### Redis Key Map

| Key Pattern | Type | TTL | Purpose |
|:---|:---|:---|:---|
| `global:state:{symbol}` | String (JSON) | 60s | Aggregated market snapshot |
| `system:heartbeat` | String | 30s | Watchdog liveness ping |
| `system:circuit_breaker` | String (JSON) | None | Global halt state |
| `system:config:regime` | String (JSON) | None | BTC regime classification |
| `system:universe:symbols` | String (JSON) | None | Active tradeable symbols |
| `system:heartbeats` | Hash | None | Per-exchange WS timestamps |
| `system:vol:floor:threshold` | String (JSON) | 3600s | Volatility floor threshold |
| `system:macro:narrator` | String (JSON) | 18000s | Macro state (RISK_ON/OFF/CHOP) |
| `karsa:position:{symbol}:{side}` | Hash | None | Active position state |
| `shadow:position:{symbol}:{side}` | JSON | None | Shadow position state |
| `ai:cache:{hash}` | String (JSON) | 300s | AI analyst result cache |
| `karsa:memory:{symbol}` | Sorted Set | None | Trade memory for AI context |
| `karsa:sector:{sector}` | String | None | Active position count per sector |
| `risk:portfolio_cb:*` | String | None | Portfolio circuit breaker state |

### Postgres Tables

**`trades`** — Complete lifecycle of every executed order:
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
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING',
    risk_snapshot JSONB NOT NULL,
    global_state_snapshot JSONB NOT NULL,
    order_id VARCHAR(50),
    exit_reason VARCHAR(50)
);
```

**`shadow_trades`** — Mirror of trades + shadow-specific columns (slippage_applied, fees_applied, is_shadow=TRUE)

**`signals`** — Every alpha signal generated (regardless of risk gate outcome)

**`system_events`** — Watchdog alerts, proxy drops, reconciliations, errors

### Pydantic Models

| Model | Purpose |
|:---|:---|
| `GlobalState` | Normalized market snapshot per symbol |
| `TradingSignal` | Alpha Bridge output (direction, confidence, metrics) |
| `RiskDecision` | Risk gate output (pass/fail, reason) |
| `TradeExecution` | Executor output (fill details, PnL) |
| `UniverseCandidate` | Universe scorer output per symbol |
| `AnalystResult` | AI CryptoAnalyst output |
| `JudgeVerdict` | AI Position Judge output |
| `MultiTFResult` | Multi-timeframe confirmation |
| `TradeMemoryEntry` | Historical trade for AI context |

---

## 8. AI Layer

### Architecture
```
┌─────────────────────────────────────────────────┐
│              9router Container                    │
│              127.0.0.1:20129                      │
│              OpenAI-compatible API                │
│                                                  │
│  ┌─────────────────┐  ┌─────────────────┐       │
│  │ CryptoAnalyst   │  │ PositionJudge   │       │
│  │ (pre-entry)     │  │ (post-entry)    │       │
│  │ claude-haiku-3-5│  │ haiku → sonnet  │       │
│  └─────────────────┘  └─────────────────┘       │
└─────────────────────────────────────────────────┘
```

### Mandatory Positions (NOT toggles)
- **CryptoAnalyst** (`analyst.py`): Pre-entry. Fetches 200 1H candles, computes TA, sends to AI. Final confidence = quant × 0.5 + ai × 0.5. Gate: >= 0.65.
- **PositionJudge** (`position_judge.py`): Post-entry. 2-tier escalation. 3 HOLDs on loser → forced EXIT.

### Fail-Safe Defaults
| Scenario | Behavior |
|:---|:---|
| AI parse failure | REJECT (never HOLD for new entries) |
| AI timeout/error | Returns 0 confidence → REJECT |
| AI unavailable | All signals rejected (mandatory means mandatory) |
| 3 consecutive HOLDs on losing position | Forced EXIT |

### Cost Estimate
~$0.60–1.20/day at 5 symbols (claude-haiku-3-5 for analyst, haiku→sonnet for judge)

---

## 9. Shadow Mode

### Architecture
Conditional component substitution in `main.py` when `SHADOW_MODE_ENABLED=true`:

| Live Component | Shadow Replacement |
|:---|:---|
| `SmartOrderRouter` | `ShadowExecutor` (simulated SOR) |
| `ActivePositionManager` | `ShadowAPM` (wick miss, funding drag) |
| `BybitClient` | `ShadowExchangeClient` (Redis-backed mock) |
| `PositionStore` | `ShadowPositionStore` (`shadow:position:*`) |
| `TradeStore` | `ShadowTradeStore` (`shadow_trades` table) |

### 4 Refinements
1. **Fee Asymmetry**: Maker (0.02%) vs taker (0.055%) based on `is_post_only`
2. **Wick Miss Prevention**: `worst_price_seen` tracked in Redis for SL detection
3. **Funding Rate Drag**: 8-hour funding fee deduction on held positions
4. **Pending Limit State Machine**: Virtual limit orders expire after 600s TTL

### State Isolation (Non-Negotiable)
- Shadow Redis keys: `shadow:position:*` — NEVER `position:*`
- Shadow trades: `shadow_trades` table — NEVER `trades`
- Startup reconciliation skipped
- Position reconciler not started

---

## 10. Risk & Safety

### Emergency Kill Switch
**Triggers:** Telegram `/kill_karsa` or file flag (`/tmp/KILL_KARSA`)
**Sequence (< 10s):**
1. Cancel all open limit orders
2. Market flatten all positions
3. Set global halt event
4. Send Telegram alert
5. `sys.exit(1)`

### Circuit Breakers

| Breaker | Trigger | Action |
|:---|:---|:---|
| Daily Drawdown | PnL ≤ -2% | HALT all entries, 4h cooldown |
| Consecutive Losses | 3 in a row | SOFT STOP, 4h cooldown |
| Execution Latency | >1500ms avg | Skip to Market orders |
| Stale Data | >15s no WS update | Pause Alpha Bridge |
| AI Unavailable | 3 consecutive failures | All signals rejected |
| ASM Session Inactive | `active == "0"` | Block executor |

### Startup Reconciliation ("Trust Nothing")
1. Fetch Bybit REST API actual positions
2. Fetch local Postgres state
3. Compare & resolve:
   - Clean → proceed
   - Orphaned orders → cancel
   - Ghost positions → overwrite Postgres with Bybit truth
   - Postgres dead → rebuild from Bybit

### Triage Sprint Features (Latest)
1. **Asymmetric Time Exits**: Losers exit in 3min, breakeven in 15min, winners no limit
2. **Free Roll Breakeven**: SL to breakeven at +0.25R (was +0.75R)
3. **Volatility Floor**: BTC 1H ATR vs 90-day 25th percentile — below threshold, block all entries
4. **Session Hard-Block**: Altcoin entries blocked 04:00-12:00 UTC
5. **AI Macro Narrator**: 4-hour cycle, RISK_ON/RISK_OFF/CHOP → sizing multiplier

---

## 11. Configuration

### Environment Variables (`.env`)

| Variable | Purpose |
|:---|:---|
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Bybit authentication |
| `POSTGRES_URL` | Database connection |
| `REDIS_URL` | Cache connection |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Bot interface |
| `WIREGUARD_*` | VPN configuration |
| `SHADOW_MODE_ENABLED` | true/false |
| `KARSA_ROLE` | data-engine / live / shadow / backtest / commander |
| `PROMETHEUS_PORT` | Metrics endpoint port |

### Confidence Profiles
Located in `config/confidence_profiles/default.yaml` — threshold configurations for signal acceptance.

---

## 12. Development

### Dev Commands
```bash
# Testing
pytest                          # Run all tests
pytest tests/triage/            # Run specific test suite

# Linting
ruff check .                    # Lint
black --check .                 # Format check
mypy --strict app/              # Type check

# Full bar (must pass before merge)
pytest && ruff check . && black --check . && mypy --strict app/
```

### Testing Strategy
- >90% unit test coverage for `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, `app/data/filters.py`
- Mandatory edge cases: empty candles, single-candle, all-flat, divide-by-zero, bad tick
- Shadow system: fee asymmetry, wick detection, funding deduction, pending limit state machine
- Phase 6 specifics: RegimeClassifier (all 4 outputs + boundaries), StrategyRouter (all 3 branches), APM (breakeven trigger, regime shift), PortfolioRiskManager (correlation cap, exposure math)

### Pre-Commit Checklist
1. `pytest` passes with 0 failures
2. `ruff check .` — 0 warnings
3. `black --check .` — 0 changes needed
4. `mypy --strict app/` — 0 errors
5. No `float` for money (only `Decimal`)
6. No hardcoded secrets
7. No `except: pass`
8. No blocking calls in asyncio code

---

## 13. Operational Procedures

### Cold Start
```bash
make up    # Starts infra + apps
```

### Rebuild Apps (After Code Changes)
```bash
make rebuild    # Rebuilds apps only, infra untouched
```

### View Logs
```bash
make logs           # App logs
make logs-infra     # Infrastructure logs
```

### DB Maintenance
```bash
make db-maintenance # Daily backup + cleanup (30d candles, 7d signals)
make db-backup      # Backup only
```

### Monitoring
- **Prometheus**: `http://localhost:9090` — metrics scraping
- **Grafana**: `http://localhost:3000` — dashboards
- **Redis**: `docker exec karsa-redis redis-cli` — state inspection
- **Postgres**: `docker exec karsa-postgres psql -U karsa` — data queries

### Troubleshooting

| Symptom | Check |
|:---|:---|
| No trades | Volatility floor blocking? Session block active? GARCH ratio? |
| VPN tunnel down | `docker logs karsa-gluetun` |
| AI not responding | `curl http://127.0.0.1:20129/health` |
| High latency | Docker resource usage? VPN routing? |
| State divergence | Run reconciliation: restart bot |
| Shadow mode issues | Verify `karsa_shadow_mode_active` metric = 1 |

---

## 14. Document Map

| Document | Purpose | Status |
|:---|:---|:---|
| `CONTEXT.md` | Orientation, glossary, open issues | Living |
| `AGENTS.md` | AI agent working rules, directory map | Living |
| `ARCHITECTURE.md` | System design, component diagrams | Approved |
| `DATA_MODEL.md` | Schemas, field names, types | Approved |
| `E2E_WORKFLOW.md` | 6-stage pipeline detail | Updated |
| `CODEBASE.md` | **This document** — comprehensive reference | New |
| `RISK_AND_RUNBOOK.md` | Kill switch, circuit breakers, recovery | Approved |
| `DEFINITION_OF_DONE.md` | Quality gates for PRs | Approved |
| `MVP_SCOPE.md` | What's in scope, phased delivery | Approved |
| `PRD.md` | Product vision (aspirational) | Draft |
| `TESTING_STRATEGY.md` | How safety claims get verified | Draft |
| `SETUP.md` | VPN setup, env vars, troubleshooting | Updated |
| `METRICS_DICTIONARY.md` | Prometheus metric definitions | Updated |
| `TELEGRAM_INTERFACE.md` | Bot commands, alert system | Draft |
| `ROADMAP.md` | Phase 0–8 delivery plan | Draft |
| `CONFIGURATION.md` | Configuration reference | Updated |

### Source-of-Truth Order
`RISK_AND_RUNBOOK.md` > `DEFINITION_OF_DONE.md` > `DATA_MODEL.md` > `ARCHITECTURE.md` > `MVP_SCOPE.md` > `PRD.md`

Safety-critical numeric conflict → **stop and ask**, never pick silently.

---

*This document is the comprehensive single-source reference for the KASM codebase. For working rules, see `AGENTS.md`. For open issues and conflicts, see `CONTEXT.md §7`.*
