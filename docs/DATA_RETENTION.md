# Data Retention Policy
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed
**Purpose:** Define the lifecycle, TTL, eviction, and archival rules for every data store in the system — Redis, PostgreSQL, AI memory, and logs.

---

## 1. Redis Data Lifecycle

### 1.1 Ephemeral State (Auto-Expiring)

| Key Pattern | TTL | Eviction | Rationale |
| :--- | :--- | :--- | :--- |
| `global:state:{symbol}` | 60s | Auto-expire | Stale market data must not survive. 60s acts as staleness guard. |
| `system:heartbeat` | 30s | Auto-expire | Watchdog detects missing heartbeat within 10s. 30s TTL = 3x safety margin. |
| `ai:cache:{hash}` | 300s | Auto-expire | AI results cached for 5 min to avoid redundant LLM calls. |
| `karsa:crypto_cooldown` | 900s | Auto-expire | 15-min post-sell cooldown. Self-clearing. |

### 1.2 Persistent State (No TTL — Manual Cleanup)

| Key Pattern | Lifecycle | Cleanup Trigger |
| :--- | :--- | :--- |
| `system:config:regime` | Overwritten every 15 min | Previous value replaced atomically |
| `system:heartbeats` (Hash) | Overwritten every WS tick | Previous value replaced atomically |
| `system:circuit_breaker` | Overwritten on state change | Reset via `/clear_halt` or circuit breaker reset |
| `system:universe:symbols` | Overwritten every 4 hours | Previous value replaced atomically |
| `karsa:position:{symbol}:{side}` | Created on fill, deleted on exit | Exit handler calls `position_store.remove()` |
| `trade:{trade_id}` | Created on trade close, never expires | Survives process restarts — permanent audit trail |
| `karsa:memory:{symbol}` | Appended on trade close | Max 20 entries per symbol (FIFO eviction via ZREMRANGEBYRANK) |
| `karsa:sector:{sector_name}` | Updated on position open/close | Rebuilt from `position_store.list_all()` on startup |
| `karsa:global_halt` | Set by `/kill_karsa` | Cleared by `/clear_halt` |
| `karsa:auto:state:active` | Set by session start | Cleared by session stop |
| `karsa:auto:config` | Set by session start | Deleted on session stop |
| `karsa:auto:start_time` | Set by session start | Deleted on session stop |
| `karsa:alerts_enabled` | Set by `/alerts` toggle | Persists until toggled |
| `karsa:settings:*` | Set by `/settings` callbacks | Persists until changed |

### 1.3 Redis Memory Estimate

| Category | Keys | Avg Size | Total |
| :--- | :--- | :--- | :--- |
| GlobalState (60 symbols x 60s TTL) | ~60 | ~500B | ~30KB |
| AI cache (5 symbols x 5min TTL) | ~5 | ~1KB | ~5KB |
| Positions (max 8 concurrent) | ~8 | ~500B | ~4KB |
| Trade memory (60 symbols x 20 entries) | ~1200 | ~200B | ~240KB |
| Bot settings | ~10 | ~100B | ~1KB |
| **Total** | | | **~280KB** |

Negligible — no memory pressure concern.

---

## 2. PostgreSQL Data Lifecycle

### 2.1 Tables

| Table | Growth Rate | Retention | Archival |
| :--- | :--- | :--- | :--- |
| `trades` | ~10-50 rows/day | Permanent | None needed (Postgres handles) |
| `signals` | ~100-500 rows/day | 90 days recommended | Archive to cold storage after 90 days |
| `system_events` | ~50-200 rows/day | 30 days recommended | Archive after 30 days |

### 2.2 Recommended Retention Queries

```sql
-- Archive old signals (run monthly)
INSERT INTO signals_archive SELECT * FROM signals WHERE created_at < NOW() - INTERVAL '90 days';
DELETE FROM signals WHERE created_at < NOW() - INTERVAL '90 days';

-- Archive old system events (run monthly)
INSERT INTO system_events_archive SELECT * FROM system_events WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM system_events WHERE created_at < NOW() - INTERVAL '30 days';
```

### 2.3 Disk Estimate

| Table | Row Size | Daily Rows | 90-Day Size |
| :--- | :--- | :--- | :--- |
| `trades` | ~2KB | ~50 | ~9MB |
| `signals` | ~1KB | ~500 | ~45MB |
| `system_events` | ~500B | ~200 | ~9MB (30 days) |
| **Total** | | | **~63MB** |

---

## 3. AI Memory Lifecycle

### 3.1 Trade Memory (`karsa:memory:{symbol}`)

| Property | Value |
| :--- | :--- |
| Storage | Redis Sorted Set |
| Max entries per symbol | 20 (FIFO eviction) |
| Entry format | JSON: `{pnl_pct, hold_min, regime, exit_reason, confidence, timestamp}` |
| Retention | Permanent (within 20-entry limit) |
| Eviction | `ZREMRANGEBYRANK` removes oldest when count > 20 |
| Rebuild source | PostgreSQL `trades` table (if Redis is lost) |

### 3.2 AI Cache (`ai:cache:{hash}`)

| Property | Value |
| :--- | :--- |
| Storage | Redis String |
| TTL | 300 seconds (5 min) |
| Purpose | Avoid redundant LLM calls within same time bucket |
| No archival | Cache is ephemeral by design |

---

## 4. Log Lifecycle

### 4.1 Application Logs

| Property | Value |
| :--- | :--- |
| Format | Structured JSON (loguru) |
| Output | stdout (captured by Docker) |
| Rotation | Docker log rotation: `max-size: 10m`, `max-file: 3` |
| Retention | ~30MB per container (3 files x 10MB) |

### 4.2 Prometheus Metrics

| Property | Value |
| :--- | :--- |
| Storage | Prometheus TSDB |
| Retention | Default 15 days |
| Scrape interval | 15 seconds |
| Storage estimate | ~100MB for 15 days at current metric count |

---

## 5. Data Recovery

### 5.1 Redis Loss

- **GlobalState:** Auto-rebuilt within 1 second (next WS tick)
- **Regime:** Auto-rebuilt within 15 minutes (next regime classification)
- **Positions:** Rebuilt from Bybit REST API on startup (reconciliation)
- **Trade memory:** Rebuilt from PostgreSQL `trades` table (if needed)
- **Bot settings:** Lost — operator must re-configure via Telegram

### 5.2 PostgreSQL Loss

- **Trades:** Lost (no external backup unless archival was configured)
- **Signals:** Lost
- **Positions:** Rebuilt from Bybit REST API on startup
- **Recovery:** Docker volume persistence (`pgdata` volume) survives container restarts. For true disaster recovery, configure periodic `pg_dump` to external storage.

### 5.3 Both Redis and PostgreSQL Loss

- System recovers from Bybit REST API truth (reconciliation)
- Historical trade data is permanently lost
- Bot settings are lost
- Trade memory is lost
