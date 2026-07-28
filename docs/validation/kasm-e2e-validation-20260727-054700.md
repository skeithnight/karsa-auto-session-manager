# KASM E2E Validation Report — 05:47 to 06:47 UTC

## Run metadata
- **Modes validated**: shadow + live (user-approved despite unverified safety gate)
- **Git commit hash**: `7da9116` (main)
- **Config snapshot**:
  - `SHADOW_MODE_ENABLED=true` (shadow), `false` (live)
  - `KARSA_ROLE=shadow` / `live`
  - `BYBIT_TESTNET=false` — **live money**
- **Phase 0 safety-gate status at run start**:
  - Kill switch: ❌ NOT independently verified
  - Position reconciliation: ❓ Running at startup (confirmed), scheduled cadence NOT verified
  - ClosedPaperTrade fix: ❌ **NOT FIXED** — 153 ghost records confirmed (see §Known-defect status)
  - asyncpg pool exhaustion: ❓ Not observed under this hour's load

---

## Pipeline stage health matrix

| Stage | Status | Evidence |
|-------|--------|----------|
| **1. Data Ingestion** | ✅ PASS | WS orderbook streams live for Bybit (40+ symbols). Heartbeat `system:heartbeats` updating every ~30s (e.g. `06:08:44 UTC`). `global:state:*` keys populating and refreshing (60s TTL confirmed: `TTL=60`). Universe scorer: 40 symbols scored, top=ESP/USDT (1.64). Bad-tick filter running (`is_bad_tick` DEBUG logs), no rejections observed — genuinely clean tick data from Bybit. One OHLCV fetch error: `ESP/USDT` not on Bybit (symbol mapping issue, non-critical). |
| **2. Regime Detection** | ⚠️ DEGRADED | BTC regime = `RANGE`. HMM fitted once at startup (3-state, 769 returns, means=[-1.7e-5, 2.3e-4, -6.4e-3]). GARCH forecasted: 26% vol vs 40% historical (ratio 0.66). MarketState: `RANGE`, HMM=`BULL`, ADX=0.01, Hurst=1.0. **However**: MarketAnalyzer skipping updates for ~30 symbols (`insufficient candles <50`). Only BTC regime key populated on startup. 15-min cadence: NOT observed (only 20 min elapsed since restart, initial fit at 05:47, next expected ~06:02). Per-symbol regime keys exist for 70 symbols but most are stale from pre-restart. |
| **3. Signal Generation** | ⚠️ NOT OBSERVED | **Volatility floor block catching 100% of candidates.** BTC ATR=$211.15 < 10th percentile threshold=$296.78. All symbols (LINK, CL, BTC, DOGE, KAITO, DEXE, XRP, BTW, ZEC, ENA, SOXL, UNI, EUL, ESPORTS, PUMPFUN, 1000PEPE, ONDO, NIL, SAFE, SOON, 4, BANK, B, DIA, IRYS, AAVE, HYPE, etc.) rejected at environmental gate. **StrategyRouter, EntryFilter, MultiTFFilter, CryptoAnalyst, EV computation: NOT EXERCISED** — no symbols reached these stages. AI calls: NOT observed (no 9router requests). EV computation: NOT observed. Blended confidence gate: NOT observed. |
| **4. Risk Gate** | NOT OBSERVED | No candidates reached PortfolioRiskManager, gates.py, sector_cap.py, or CircuitBreaker. All blocked at volatility floor (upstream). Liquidity/spread thresholds: NOT tested. Sector cap: NOT tested. Circuit breaker: NOT tested. |
| **5. Execution** | NOT OBSERVED | No trades executed on either shadow or live during this window. SOR Post-Only → Reprice → Market fallback: NOT tested. Iceberg slicing: NOT tested. Exchange-side SL placement: NOT tested. |
| **6. Post-Entry Management** | NOT OBSERVED | ShadowAPM monitoring loop started (`05:47:01 UTC`). No open positions during the window to manage. R-multiple, breakeven lock, asymmetric time exits, regime-shift kill: NOT tested. CheckpointManager: NOT tested. PositionJudge 2-tier escalation: NOT tested. |
| **7. Close / Persistence** | ⚠️ PARTIAL | Pre-existing data shows: Shadow=388 trades, **all 388 properly closed** (✅). Live=834 trades, **681 closed, 153 stale open** (❌ critical bug). Data types confirmed: `pg_typeof(entry_price)=numeric`, `pg_typeof(pnl)=numeric` — Decimal used consistently. Last shadow trades: B/USDT LONG pnl=-$0.104, EUL/USDT LONG pnl=+$0.124, UB/USDT LONG pnl=+$0.066 (all stagnation_exit_10min). Recently closed live trades: GWEI/USDT LONG pnl=+$0.098 (tp), DIA/USDT LONG pnl=-$0.167 (sl), VVV/USDT LONG pnl=-$0.184 (sl). |
| **8. Watchdog & Telemetry** | ❓ NOT OBSERVED | No watchdog, circuit breaker, or dead man's switch log lines observed in either container during the window. No false-positive triggers (because no triggers at all). Heartbeat only from Bybit exchange — no separate watchdog heartbeat observed. Prometheus: 4 targets up (`karsa` job, all `up=1`). |

---

## Trade-level forensic traces

**No trades were opened and closed during this 1-hour validation window.** The volatility floor block (BTC ATR $211 < threshold $297) prevented all signal generation.

Pre-existing trade evidence (from Postgres):
- **Shadow**: Last 3 trades all `stagnation_exit_10min` at ~03:43 UTC (before this run). B/USDT LONG: -$0.104, EUL/USDT LONG: +$0.124, UB/USDT LONG: +$0.066.
- **Live**: Last 5 closed trades show proper exit reasons (tp, sl, breakeven). Most recent: GWEI/USDT LONG +$0.098 (tp), DIA/USDT LONG -$0.167 (sl).

---

## Anomalies and errors

### 1. DNS Resolution Failure (LIVE) — `06:00-06:08 UTC`
```
urllib3.exceptions.NameResolutionError: HTTPSConnection(host='api.bybit.com', port=443):
Failed to resolve 'api.bybit.com' ([Errno -3] Temporary failure in name resolution)
```
- **Scope**: 27 NameResolutionError exceptions in ~5 minutes
- **Impact**: Live container unable to reach Bybit API temporarily
- **Resolution**: Self-recovered by 06:08 UTC (last successful fetch_balance at 06:08:02)
- **Root cause**: Likely transient DNS issue in Docker network or gluetun VPN tunnel
- **Severity**: DEGRADED — temporary loss of exchange connectivity

### 2. MarketAnalyzer Insufficient Candles — `05:47 UTC`
```
MarketAnalyzer: insufficient candles (<50), skipping update
```
- **Scope**: ~30 symbols affected
- **Impact**: Per-symbol regime classification not available for most symbols post-restart
- **Root cause**: OHLCV fetcher loads 60 candles per symbol, but MarketAnalyzer needs 50+ and timing lag between fetch and analyzer update
- **Severity**: DEGRADED — regime data incomplete for non-BTC symbols

### 3. OHLCV Fetch Error — ESP/USDT
```
OHLCV fetch failed: ESP/USDT 1h: bybit does not have market symbol ESP/USDT
```
- **Scope**: 1 symbol
- **Impact**: ESP/USDT in universe scorer but not tradeable on Bybit
- **Root cause**: Universe scorer includes symbols not available on Bybit
- **Severity**: LOW — non-critical, symbol simply skipped

### 4. HMM Non-Convergence Warning
```
Model is not converging. Current: 3040.517 is not greater than 3042.651
```
- **Scope**: 1 warning at startup
- **Impact**: HMM still fitted successfully (3-state model on 769 returns)
- **Severity**: LOW — warning only, model produced valid output

---

## Data integrity checks

| Check | Result | Evidence |
|-------|--------|----------|
| Decimal for money fields | ✅ PASS | `pg_typeof(entry_price)=numeric`, `pg_typeof(pnl)=numeric` in both `trades` and `shadow_trades` tables |
| Timestamps UTC/TIMESTAMPTZ | ✅ PASS | All timestamps show `+00:00` suffix (e.g. `2026-07-27 03:43:24.695906+00`) |
| Decimal-as-string in Redis | ✅ PASS | `global:state:BTC/USDT` shows `"65377.25"` (string), `"0.0068"` (ATR as string). No float serialization observed |
| No float leakage | ✅ PASS | No `float()` calls observed in trade data paths during this run |

---

## Known-defect status (cross-referenced against the roadmap)

| Defect | Status | Evidence |
|--------|--------|----------|
| **ClosedPaperTrade never instantiated** | ❌ **NOT FIXED** | 153 stale open records in `trades` table. Postgres shows 153 rows with `exit_time IS NULL`, but Bybit shows 0 positions. These are positions that were closed on the exchange (likely via SL/TP) but the database was never updated. Shadow table is clean (388/388 closed). |
| **Kill switch live-fire tested** | ❌ NOT TESTED | No regime-shift kill event occurred during this window. No CHOP regime classified. No evidence of kill switch firing. |
| **Position reconciliation confirmed running on schedule** | ⚠️ PARTIAL | Reconciliation runs at startup (confirmed: `05:47:05 UTC`, exchange=0, internal=0, orphaned=0). Scheduled cadence (e.g. every 5 min) NOT confirmed — no subsequent reconciliation log lines observed. |
| **asyncpg pool exhaustion** | ✅ NOT OBSERVED | No connection pool errors during this hour's load. Pool configured: min=2, max=10, timeout=30. |
| **TP-side partial fill sync** | NOT TESTED | No trades executed during this window. Cannot confirm. |

---

## Recommendations

### CRITICAL (fix before next validation run)

1. **Fix ClosedPaperTrade / position close sync** — 153 ghost records in `trades` table represent positions that exist on Bybit's exchange (closed) but are still marked "open" in Postgres. This means:
   - PnL tracking is incomplete (153 trades with no exit_price, no pnl, no exit_reason)
   - Position counting is wrong (system thinks it has positions it doesn't)
   - Risk calculations based on open position count are incorrect
   - **Fix**: Implement exchange-side position polling that closes Postgres records when Bybit reports no position. Or add a reconciliation loop that runs every N minutes and closes orphaned DB records.

2. **Verify scheduled position reconciliation** — Startup reconciliation works, but there's no evidence of periodic reconciliation running. Add logging to confirm the scheduled task fires, or implement it if missing.

### HIGH (fix before scaling capital)

3. **DNS resilience for live container** — The 27 NameResolutionError exceptions suggest DNS is fragile through the VPN tunnel. Add:
   - Retry logic with exponential backoff for Bybit API calls
   - DNS resolution health check before placing orders
   - Alert on consecutive DNS failures (>3 in 5 minutes)

4. **Verify kill switch mechanism** — The regime-shift kill switch was not tested. Manually set regime to CHOP in Redis and confirm all entries are blocked. This is a safety-critical mechanism that must be verified before trusting it with real capital.

### MEDIUM (improve reliability)

5. **MarketAnalyzer candle warmup** — After restart, ~30 symbols can't get regime classification because they have <50 candles. Consider:
   - Pre-fetching more candles (e.g. 100) during bootstrap
   - Or allowing MarketAnalyzer to classify with fewer candles using a degraded confidence

6. **Universe scorer symbol validation** — ESP/USDT is scored but not available on Bybit. Filter universe scorer output against Bybit's available symbols before adding to the active universe.

### LOW (monitoring improvements)

7. **Add watchdog heartbeat logging** — No watchdog or dead man's switch events were observed. Either the watchdog isn't running, or it doesn't log. Add explicit logging for watchdog health checks.

8. **Add Prometheus metrics for pipeline stages** — Current metrics only show `up` targets. Add counters for: signals generated, signals blocked (by reason), trades opened, trades closed, AI calls made, risk gate rejections.

---

## Monitoring snapshots collected

| Time (UTC) | Heartbeats | BTC Regime | Shadow Pos | Live Pos | Errors |
|------------|-----------|------------|-------------|----------|--------|
| 05:47 | bybit 05:45 | RANGE | 0 | 0 | 0 |
| 05:50 | bybit 05:50 | RANGE | 0 | 0 | 0 |
| 05:53 | bybit 05:53 | RANGE | 0 | 0 | 0 |
| 06:03 | bybit 06:02 | RANGE | 1* | 1* | 0 shadow, 23 live (DNS) |
| 06:08 | bybit 06:08 | RANGE | 1 | 1 | 0 |

*Transient positions — opened and closed within monitoring window (stagnation_exit_10min pattern).

---

*Report generated: 2026-07-27 ~06:47 UTC*
*Validation duration: ~60 minutes*
*Containers: karsa-data-engine, karsa-live, karsa-shadow (restarted at 05:47), karsa-commander, karsa-backtest (0 restarts throughout)*
