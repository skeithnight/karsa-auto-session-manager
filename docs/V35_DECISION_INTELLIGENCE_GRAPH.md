# Karsa v3.5 Decision Intelligence Graph

**Version**: 3.5  
**Status**: Production (Confidence Filter Mode)  
**Last Updated**: 2026-07-28

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Components](#core-components)
3. [Evaluator System](#evaluator-system)
4. [Fusion Layer](#fusion-layer)
5. [Evidence TTL](#evidence-ttl)
6. [Execution Intent](#execution-intent)
7. [Integration with DecisionEngine](#integration-with-decisionengine)
8. [Configuration](#configuration)
9. [Deployment](#deployment)
10. [Monitoring](#monitoring)
11. [Watchdog & Infrastructure Recovery](#watchdog--infrastructure-recovery)
12. [5-Hour Observation Results](#5-hour-observation-results)
13. [Future Work](#future-work)

---

## Architecture Overview

### System Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Decision Intelligence Graph                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Market Data → Features → Independent Evaluators → Fusion → Score → Gate    │
│       │            │              │                    │         │            │
│       ▼            ▼              ▼                    ▼         ▼            │
│  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  ┌─────────┐  ┌─────────┐   │
│  │ Snapshot │  │ Feature │  │ TrendEvaluator  │  │ Fusion  │  │ Scoring │   │
│  │         │  │ Vector  │  │ VolEvaluator    │  │ Result  │  │ Bridge  │   │
│  └─────────┘  └─────────┘  │ MomEvaluator    │  └─────────┘  └─────────┘   │
│                             │ PortEvaluator   │                              │
│                             └─────────────────┘                              │
│                                      │                                       │
│                                      ▼                                       │
│                             ┌─────────────────┐                              │
│                             │  ExecutionIntent │                              │
│                             │  (Immutable)     │                              │
│                             └─────────────────┘                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Modularity**: Each evaluator is independent and testable
2. **Composability**: Evaluators can be combined via fusion layer
3. **Immutability**: ExecutionIntent creates new versions, never mutates
4. **Freshness Tracking**: Evidence TTL tracks data freshness
5. **Graceful Degradation**: v3.5 is optional and falls back to existing scoring
6. **Audit Trail**: IntentHistory tracks full version chain
7. **Regime-Aware**: Weights adjust based on market regime

---

## Core Components

### File Structure

```
app/
├── alpha/
│   └── evaluators/
│       ├── __init__.py
│       ├── trend_evaluator.py      # Trend analysis
│       ├── volatility_evaluator.py # Volatility regime
│       ├── momentum_evaluator.py   # Momentum analysis
│       ├── portfolio_evaluator.py  # Portfolio risk
│       ├── fusion.py               # Fusion layer
│       ├── registry.py             # Evaluator registry
│       ├── scoring_bridge.py       # Bridge to DecisionEngine
│       ├── integration.py          # Alternative integration
│       └── demo_v35.py             # Full pipeline demo
├── core/
│   ├── evidence_ttl.py             # Evidence freshness tracking
│   └── execution_intent.py         # Immutable execution intents
└── consumer/
    └── decision_engine.py          # Integration point
```

---

## Evaluator System

### EvaluatorRegistry (`app/alpha/evaluators/registry.py`)

**Purpose**: Central registry for managing evaluators and running fusion.

**Key Methods**:
- `evaluate(features, snapshot, regime)` - Run all evaluators and fuse results
- `evaluate_single(evaluator_name, features, snapshot)` - Run a single evaluator
- `get_evaluator(name)` - Get evaluator by name
- `register_evaluator(name, evaluator)` - Register custom evaluators
- `get_available_evaluators()` - List available evaluators

**Regime-Specific Weights**:

| Regime | Trend | Volatility | Momentum | Portfolio |
|--------|-------|------------|----------|-----------|
| TREND_BULL | 1.2 | 0.8 | 1.1 | 0.9 |
| TREND_BEAR | 1.2 | 0.8 | 1.1 | 0.9 |
| RANGE | 0.8 | 1.2 | 0.9 | 1.1 |
| HYPER_BULL | 1.0 | 1.3 | 1.2 | 0.8 |
| HYPER_BEAR | 1.0 | 1.3 | 1.2 | 0.8 |

---

### TrendEvaluator (`app/alpha/evaluators/trend_evaluator.py`)

**Purpose**: Evaluates trend strength and direction.

**Components**:
- EMA 20/200 Crossover (weight: 25)
- ADX Trend Strength (weight: 20)
- Price vs Moving Averages (weight: 20)
- Volume Confirmation (weight: 15)
- RSI Momentum (weight: 20)

**Output**:
- `direction`: 1.0 (bull), -1.0 (bear), 0.0 (neutral)
- `weight`: Signal strength (0-100)
- `confidence`: Confidence level (0-1)
- `reason`: Human-readable explanation

**Example**:
```python
evaluator = TrendEvaluator()
result = evaluator.evaluate(features, snapshot)

if result.is_actionable:
    print(f"Trend: {result.direction}, Weight: {result.weight}, Confidence: {result.confidence}")
    print(f"Reason: {result.reason}")
```

---

### VolatilityEvaluator (`app/alpha/evaluators/volatility_evaluator.py`)

**Purpose**: Evaluates volatility regime and its implications for position management.

**Components**:
- ATR Percentile (weight: 30)
- ATR% Normalized (weight: 25)
- Hurst Exponent (weight: 25)
- Price Range (weight: 20)

**Key Questions**:
- Is the market volatile enough for trend trades?
- Should we expect choppy/ranging behavior?
- Is volatility expanding or contracting?

---

### MomentumEvaluator (`app/alpha/evaluators/momentum_evaluator.py`)

**Purpose**: Evaluates momentum and its implications for position management.

**Components**:
- RSI Momentum (weight: 30)
- CVD Slope (weight: 25)
- Price Momentum (weight: 25)
- Volume Momentum (weight: 20)

**Key Questions**:
- Is there strong directional momentum?
- Are we seeing exhaustion/reversal signals?
- Is volume confirming the price move?

---

### PortfolioEvaluator (`app/alpha/evaluators/portfolio_evaluator.py`)

**Purpose**: Evaluates portfolio state and its implications for position management.

**Components**:
- Drawdown Level (weight: 30)
- Position Count (weight: 20)
- Sector Concentration (weight: 20)
- Correlation (weight: 15)
- Cash Reserve (weight: 15)

**Key Questions**:
- Is the portfolio in a good state to take new positions?
- Should we be defensive?
- Are we over-concentrated in any sector?

---

## Fusion Layer

### EvaluatorFusion (`app/alpha/evaluators/fusion.py`)

**Purpose**: Combines results from multiple independent evaluators into a final recommendation.

**Key Methods**:
- `fuse(results, evaluator_names)` - Main fusion method
- `_compute_confidence(results)` - Confidence from evaluator agreement
- `_compute_attribution(results, names)` - Attribution breakdown
- `_map_to_recommendation(direction, weight, confidence)` - Maps to action
- `_generate_reasoning(...)` - Human-readable reasoning

**FusionResult** (frozen dataclass):
```python
@dataclass(frozen=True)
class FusionResult:
    direction: float      # -1.0 to 1.0
    weight: float         # 0-100
    confidence: float     # 0-1
    attribution: dict     # Decision attribution breakdown
    recommendation: str   # HOLD, REDUCE, EXIT, INCREASE, TRAIL, FREEZE
    reasoning: str        # Human-readable explanation

    @property
    def is_actionable(self) -> bool:
        return self.weight > 15.0 and self.confidence > 0.4

    @property
    def dominant_evaluator(self) -> str:
        # Which evaluator contributed most
```

**Recommendation Mapping**:
- `INCREASE`: direction > 0.3 AND weight > 30 AND confidence > 0.6
- `REDUCE`: direction < -0.3 AND weight > 30 AND confidence > 0.6
- `EXIT`: weight > 50 AND confidence > 0.7
- `TRAIL`: weight > 20 AND confidence > 0.5
- `FREEZE`: confidence < 0.3
- `HOLD`: default

---

## Evidence TTL

### EvidenceTTL (`app/core/evidence_ttl.py`)

**Purpose**: Adds freshness tracking to evidence sources.

**Freshness Levels**:
- `FRESH`: TTL remaining > 50%
- `ACCEPTABLE`: TTL remaining 20-50%
- `STALE`: TTL remaining 0-20%
- `EXPIRED`: TTL remaining <= 0

**Default TTL Values**:

| Source | TTL | Category |
|--------|-----|----------|
| price | 0 | Hot path |
| orderbook | 0.5s | Hot path |
| spread | 0.5s | Hot path |
| cvd | 1s | Hot path |
| funding | 8h | Warm path |
| oi | 1min | Warm path |
| liquidations | 30s | Warm path |
| volume | 5s | Warm path |
| regime | 5min | Cold path |
| atr | 5min | Cold path |
| rsi | 1min | Cold path |
| adx | 5min | Cold path |
| news | 30min | Very cold |
| macro | 1h | Very cold |
| ai | 5min | Very cold |
| portfolio | 10s | Portfolio |
| correlation | 5min | Portfolio |
| sector | 10min | Portfolio |

**Usage**:
```python
from app.core.evidence_ttl import EvidenceTTLRegistry

registry = EvidenceTTLRegistry()

# Collect evidence with TTL
registry.collect(
    source="orderbook",
    value=1.0,
    weight=15.0,
    description="Strong bid support"
)

# Get usable evidence (filters expired)
usable = registry.get_usable()

# Get weighted score with freshness adjustment
score = registry.compute_weighted_score()
```

---

## Execution Intent

### ExecutionIntent (`app/core/execution_intent.py`)

**Purpose**: Replaces mutable position state mutations with immutable intent objects.

**Intent Goals**:
- `OPEN_POSITION`
- `CLOSE_POSITION`
- `REDUCE_EXPOSURE`
- `INCREASE_EXPOSURE`
- `MOVE_STOP`
- `MOVE_TARGET`
- `TRAIL_STOP`
- `HEDGE`
- `REBALANCE`

**Intent Urgency**:
- `LOW`: Best effort, maker preferred
- `NORMAL`: Standard execution
- `HIGH`: Speed matters, accept some slippage
- `CRITICAL`: Must fill immediately, market order

**Immutable Versioning**:
```python
# Create initial intent
intent = ExecutionIntent(
    symbol="BTCUSDT",
    side="LONG",
    quantity=Decimal("0.001"),
    goal=IntentGoal.OPEN_POSITION,
    constraints=IntentConstraints(
        max_slippage_pct=Decimal("0.003"),
        maker_preferred=True,
        time_budget_seconds=12,
    ),
)

# Create new version (never mutates original)
intent_v2 = intent.with_updates(
    goal=IntentGoal.REDUCE_EXPOSURE,
    target_exposure=Decimal("0.5"),
    reason="Take partial profit",
)

# Mark as executed
intent_v3 = intent_v2.mark_executed(
    result=IntentResult(
        filled_quantity=Decimal("0.0005"),
        average_price=Decimal("50000"),
        total_fees=Decimal("0.025"),
        slippage_bps=2.5,
        maker_fill=True,
        fill_time_ms=150.0,
        order_ids=["order_123"],
    )
)
```

**Audit Trail**:
```python
from app.core.execution_intent import IntentHistory

history = IntentHistory()
history.add(intent)
history.add(intent_v2)
history.add(intent_v3)

# Get full version chain
chain = history.get_chain(intent.intent_id)
# Returns [intent, intent_v2, intent_v3]

# Get latest version
latest = history.get_latest(intent.intent_id)
# Returns intent_v3
```

---

## Integration with DecisionEngine

### ScoringBridge (`app/alpha/evaluators/scoring_bridge.py`)

**Purpose**: Bridge between new v3.5 evaluator architecture and existing DecisionEngine scoring logic.

**Key Methods**:
- `score(features, snapshot, regime, fallback_score)` - Compute score using v3.5 evaluators or fallback
- `enable()` / `disable()` - Toggle v3.5 scoring
- `should_trade(fusion_result, min_score)` - Determine if we should trade
- `get_recommendation(fusion_result)` - Get recommendation from fusion result
- `_fusion_to_score(fusion_result)` - Convert fusion result to score format (0-100)

**Integration in DecisionEngine**:

```python
# In DecisionEngine.__init__()
from app.alpha.evaluators.scoring_bridge import ScoringBridge
from app.core.config import get_settings

_settings = get_settings()
self._scoring_bridge = ScoringBridge(enabled=_settings.evaluator_v35_enabled)

# In DecisionEngine.evaluate()
existing_score = context.total_confidence * vol_floor_penalty

# v3.5 evaluator scoring (if enabled, falls back to existing_score)
v35_score, fusion_result = await self._scoring_bridge.score(
    features, snapshot, regime, fallback_score=existing_score
)

# v3.5 as Confidence Filter
v35_confidence_threshold = 0.6
if fusion_result and fusion_result.confidence < v35_confidence_threshold:
    logger.info(
        "evaluate: %s %s REJECTED by v3.5 confidence filter (%.2f < %.2f)",
        symbol, direction, fusion_result.confidence, v35_confidence_threshold,
    )
    ObservabilityLogger.log_reject_reason(
        symbol, "v3.5 Confidence Filter",
        {"confidence": fusion_result.confidence, "threshold": v35_confidence_threshold}
    )
    continue

# Use StrategyRouter score (not v3.5 score) for gate comparison
score = existing_score

# Log v3.5 evaluation for monitoring
if fusion_result:
    logger.info(
        "evaluate: %s %s v3.5 score=%.1f confidence=%.2f recommendation=%s (using StrategyRouter score=%.1f)",
        symbol, direction, v35_score, fusion_result.confidence,
        fusion_result.recommendation, score,
    )
```

---

## Configuration

### Environment Variables

```bash
# Enable v3.5 evaluator scoring
EVALUATOR_V35_ENABLED=true

# Confidence threshold for filter (default: 0.6)
V35_CONFIDENCE_THRESHOLD=0.6
```

### Config Settings (`app/core/config.py`)

```python
# v3.5 Decision Evaluation Graph
evaluator_v35_enabled: bool = False  # Enable v3.5 evaluator scoring
```

---

## Deployment

### Enable v3.5

1. Add to `.env`:
```bash
EVALUATOR_V35_ENABLED=true
```

2. Rebuild apps:
```bash
make rebuild
```

3. Verify in logs:
```
evaluate: BTC/USDT LONG v3.5 score=15.4 confidence=0.72 recommendation=HOLD (using StrategyRouter score=40.0)
```

### Disable v3.5

1. Remove from `.env` or set to `false`:
```bash
EVALUATOR_V35_ENABLED=false
```

2. Rebuild apps:
```bash
make rebuild
```

---

## Monitoring

### Log Format

```
evaluate: {symbol} {direction} v3.5 score={score} confidence={confidence} recommendation={recommendation} (using StrategyRouter score={score})
```

### Key Metrics

| Metric | Description |
|--------|-------------|
| v3.5 score | Fusion result weight (0-100) |
| v3.5 confidence | Fusion result confidence (0-1) |
| v3.5 recommendation | HOLD, REDUCE, EXIT, INCREASE, TRAIL, FREEZE |
| StrategyRouter score | Original scoring for gate comparison |

### Rejection Logs

```
evaluate: {symbol} {direction} REJECTED by v3.5 confidence filter ({confidence} < {threshold})
```

---

## Watchdog & Infrastructure Recovery

### Watchdog Capabilities (`app/watchdog/monitor.py`)

The Watchdog monitors **application-level** health:

| Check | What It Does | Action |
|-------|--------------|--------|
| Heartbeat Monitor | Checks exchange data freshness | Pauses Alpha Bridge on stale data |
| Execution Latency | Tracks signal→fill latency | Switches SOR to market-only if >1500ms |
| Event Loop Lag | Monitors asyncio responsiveness | Flattens positions on sustained lag (>100ms × 3) |
| Critical Tasks | Monitors registered async tasks | Logs death, sets metric |
| Memory | Tracks RSS memory usage | Logs metric (no action) |

### What Watchdog Does NOT Do

❌ **Does NOT monitor infrastructure** (Redis, PostgreSQL, Docker containers)  
❌ **Does NOT restart containers**  
❌ **Does NOT detect Redis crashes**  

### Why Redis Isn't Auto-Recovering

**Root Cause**: The Watchdog is an **application-level monitor**, not an infrastructure monitor.

**Architecture Gap**:
```
┌─────────────────────────────────────────────────────────────┐
│                    Infrastructure Layer                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │  Redis  │  │ Postgres│  │ Gluetun │  │ 9Router │        │
│  │ restart:│  │ restart:│  │ restart:│  │ restart:│        │
│  │unless-  │  │unless-  │  │unless-  │  │unless-  │        │
│  │stopped  │  │stopped  │  │stopped  │  │stopped  │        │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │
│                                                              │
│  Docker `restart: unless-stopped` policy handles crashes     │
│  BUT: Manual `stop` or `down` overrides this policy          │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ No monitoring
                            │
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    Watchdog                          │   │
│  │  • Heartbeat check (app-level)                       │   │
│  │  • Latency tracking                                  │   │
│  │  • Event loop lag                                    │   │
│  │  • Critical task liveness                            │   │
│  │                                                      │   │
│  │  ❌ Does NOT check Redis connectivity                │   │
│  │  ❌ Does NOT restart infrastructure                  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Docker Restart Policy

Redis has `restart: unless-stopped` in `docker-compose.infra.yml`:
```yaml
redis:
  image: redis:7-alpine
  container_name: karsa-redis
  restart: unless-stopped
  command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
  healthcheck:
    test: [ "CMD", "redis-cli", "ping" ]
    interval: 5s
    timeout: 3s
    retries: 10
```

**This means**:
- ✅ Auto-restarts on crash (exit code non-zero)
- ✅ Auto-restarts on Docker daemon restart
- ❌ Does NOT auto-restart after `docker compose stop` or `docker compose down`
- ❌ Does NOT auto-restart after `docker stop karsa-redis`

### Current Status (2026-07-28)

**Redis is actually running**:
```
$ docker ps -a --filter "name=karsa-redis"
karsa-redis Up 2 days

$ docker exec karsa-redis redis-cli ping
PONG
```

**Initial `docker compose ps` didn't show Redis** because it was started separately, not via the apps compose file.

### Recommendations

**Option 1: Add Infrastructure Health Check to Watchdog** (Recommended)
```python
async def _check_redis_health(self) -> None:
    """Check Redis connectivity. Alert on failure."""
    try:
        await self.redis.ping()
    except Exception as e:
        logger.critical(f"Redis unreachable: {e}")
        # Could trigger alerting or restart logic
```

**Option 2: Use Docker Compose Profiles**
```bash
# Start everything together
docker compose -f docker-compose.infra.yml -f docker-compose.apps.yml up -d
```

**Option 3: Add to Makefile**
```makefile
infra-check:
	@docker exec karsa-redis redis-cli ping || (echo "Redis down, restarting..." && docker compose -f docker-compose.infra.yml up -d redis)
```

---

## 5-Hour Observation Results

### Capture Summary (2026-07-27 12:00 - 17:00)

| Metric | Value |
|--------|-------|
| Snapshots | 273 |
| Duration | 300 minutes (5 hours) |
| Errors | None |
| Wallet Captures | 2 (13:42, 13:53) |

### Redis State (2026-07-28 14:50)

| Key Category | Keys Found | Status |
|--------------|------------|--------|
| ELO | `karsa:elo:RANGE:LONG`, `karsa:elo:RANGE:SHORT` | ✅ Populated |
| Vol Surface | `karsa:vol_surface:{btc,eth,spread,composite}` | ✅ Populated |
| Ranking | `karsa:ranking:decision` = `❌ REJECT` | ✅ Active |
| Gate | `karsa:gate:dynamic_threshold` = 50.0 | ✅ Calibrated |
| System Regime | `system:regime:{symbol}` for 40+ symbols | ✅ Populated |
| Correlation | None | ⚠️ No correlated positions |
| Positions | None | ✅ No open positions |

### Gate Decision

```
Ranking Decision: ❌ REJECT
Dynamic Threshold: 50.0 (median_ev=0.0983, winning=22/200)
```

**Interpretation**: The RankingEngine has rejected the current strategy due to insufficient edge (median EV = 9.8%). This is working as designed — the system is protecting capital during low-edge conditions.

### v3.5 Live Scores (from earlier monitoring)

| Symbol | StrategyRouter | v3.5 Score | v3.5 Confidence | Recommendation |
|--------|---------------|------------|-----------------|----------------|
| BTC/USDT | 40-70 | 14-21 | 0.72-0.75 | HOLD |
| ETH/USDT | 40-70 | 7-21 | 0.72-0.75 | HOLD |
| SOL/USDT | 70 | 21-27 | 0.75 | HOLD |

**Key Insight**: v3.5 confidence is consistently 0.72-0.75, above the 0.6 filter threshold. No rejections occurred. StrategyRouter scores (40-70) are used for gate comparison, and all are below the gate threshold (75), so no trades are taken.

---

## Future Work

### Phase 1: Enhanced Monitoring
- [ ] Dashboard for v3.5 vs StrategyRouter score comparison
- [ ] Confidence distribution analysis
- [ ] Rejection rate tracking

### Phase 2: Adaptive Thresholds
- [ ] Dynamic confidence threshold based on market conditions
- [ ] Regime-specific confidence thresholds
- [ ] Historical performance-based calibration

### Phase 3: Additional Evaluators
- [ ] Orderbook depth evaluator
- [ ] Funding rate evaluator
- [ ] Cross-asset momentum evaluator
- [ ] News sentiment evaluator

### Phase 4: ExecutionIntent Integration
- [ ] Connect ExecutionIntent to SOR/APM
- [ ] Versioned execution audit trail
- [ ] Replay capability for debugging

### Phase 5: Learning Engine
- [ ] Counterfactual analysis (what would have happened?)
- [ ] Evaluator performance tracking
- [ ] Automatic weight adjustment based on outcomes

### Phase 6: Infrastructure Monitoring
- [ ] Add Redis health check to Watchdog
- [ ] Add PostgreSQL health check to Watchdog
- [ ] Infrastructure restart capability
- [ ] Alerting on infrastructure failures

---

## Testing

### Unit Tests

```bash
# Run v3.5 prototype tests
uv run pytest tests/test_v3_prototypes/ -v

# Run specific evaluator tests
uv run pytest tests/test_v3_prototypes/test_trend_evaluator.py -v
uv run pytest tests/test_v3_prototypes/test_volatility_evaluator.py -v
uv run pytest tests/test_v3_prototypes/test_momentum_evaluator.py -v
uv run pytest tests/test_v3_prototypes/test_portfolio_evaluator.py -v
uv run pytest tests/test_v3_prototypes/test_fusion.py -v
uv run pytest tests/test_v3_prototypes/test_scoring_bridge.py -v
```

### Integration Tests

```bash
# Run demo
uv run python -m app.alpha.evaluators.demo_v35
```

---

## Troubleshooting

### Issue: v3.5 scores always below gate threshold

**Symptom**: No trades taken even when StrategyRouter would have triggered.

**Cause**: v3.5 scores (6-28) are naturally lower than StrategyRouter scores (40-70).

**Solution**: This is expected behavior. v3.5 is used as a confidence filter, not a replacement. StrategyRouter scores are still used for gate comparison.

### Issue: All evaluations pass confidence filter

**Symptom**: No rejections from v3.5 confidence filter.

**Cause**: v3.5 confidence is consistently 0.72-0.75, above the 0.6 threshold.

**Solution**: This is expected behavior. The filter only rejects when confidence drops below threshold, which hasn't happened in current market conditions.

### Issue: v3.5 not logging

**Symptom**: No v3.5 log entries in live logs.

**Cause**: `EVALUATOR_V35_ENABLED` not set or set to `false`.

**Solution**: Ensure `EVALUATOR_V35_ENABLED=true` in `.env` and rebuild.

### Issue: Redis not showing in `docker compose ps`

**Symptom**: `docker compose -f docker-compose.infra.yml ps` doesn't show Redis.

**Cause**: Redis was started separately or via different mechanism.

**Solution**: Check with `docker ps -a --filter "name=karsa-redis"`. Redis may be running but not managed by the current compose context.

---

## References

- [Integration Guide](v35_integration_guide.md)
- [DecisionEngine Code](../app/consumer/decision_engine.py)
- [ScoringBridge Code](../app/alpha/evaluators/scoring_bridge.py)
- [Fusion Layer Code](../app/alpha/evaluators/fusion.py)
- [Evidence TTL Code](../app/core/evidence_ttl.py)
- [ExecutionIntent Code](../app/core/execution_intent.py)
- [Watchdog Code](../app/watchdog/monitor.py)
- [Docker Infra](../docker-compose.infra.yml)

---

*Document generated from codebase analysis. Last updated: 2026-07-28*
