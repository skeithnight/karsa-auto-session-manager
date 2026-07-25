# 🔬 2-HOUR FORENSIC REPORT — Pre-Soft-Gate Baseline Capture

**Capture Period:** 2026-07-24 18:04:01 UTC → 20:05:51 UTC (2h 01m 50s)
**Snapshots:** 240 (1 per 30s)
**Containers:** karsa-live (PID 5489), karsa-shadow (PID 5491), karsa-data-engine (PID 5495)
**Purpose:** Baseline capture before Soft Gate / Big Gainers Alpha deployment

---

## 📊 EXECUTIVE SUMMARY

| Category | Verdict | Details |
|----------|---------|---------|
| **E2E Pipeline** | ✅ HEALTHY | Data → Decision → Risk → Execution all flowing |
| **Position Cap** | ✅ ENFORCED | Live: 4 → 1 (gradual drawdown), Shadow: 1–6 |
| **Phantom Loops** | 🔴 CRITICAL | TOWNS/USDT phantom trade — APM looped 8x before exit detection |
| **Regime Shift Kill** | ⚠️ 2 FIRED | BANK/USDT and ACE/USDT killed on → RANGE shift |
| **Orphan Grace Period** | ⚠️ 2 ORPHANS | BANK/USDT and ACE/USDT synced from Bybit, then immediately killed |
| **AI Analyst** | ✅ INTERMITTENT | All signals passed AI in live (no rejections logged); shadow AI analysis working |
| **APM Management** | ⚠️ MIXED | 3 stagnation exits (correct), 2 regime kills (correct), 1 phantom loop (bug) |
| **VPN/DNS** | ✅ HEALTHY | 1 transient pybit timeout (18:35), auto-recovered |
| **Data Engine** | ⚠️ UNIVERSE SCANNER SPAM | ~900 DEBUG lines from UniverseScanner.refresh flooding logs |

---

## 📈 POSITION TIMELINE

| Time (UTC) | Live | Shadow | Event |
|------------|------|--------|-------|
| 18:04:01 | 4 | 2 | Capture start. Live: GWEI, TOWNS, BANK, ACE. Shadow: BANK, AKE |
| 18:11:22 | 4 | 0 | **Shadow stagnation exits**: BANK/USDT, AKE/USDT (10min R=0.00) |
| 18:11:41 | 4 | 0 | APM: GWEI/USDT half-breakeven locked at 0.02507 |
| 18:16:01 | 4→3 | 0 | 🔴 **TOWNS/USDT phantom trade detected** — 8 APM reconcile loops before exit |
| 18:16:09 | 3 | 0 | TOWNS/USDT exit detected (SL hit, pnl=-$0.37, -3.22%) |
| 18:21:23 | 3→2 | 0 | **BANK/USDT stagnation exit** (30min, R=-0.09) |
| 18:22:27 | 2→1 | 0 | **BANK/USDT orphan synced** from Bybit → immediately regime shift killed |
| 18:22:42 | 1 | 0 | BANK/USDT force closed — regime_shift__to_RANGE |
| 18:22:43 | 1 | 0 | ⚠️ trade_memory store FAILED for BANK/USDT: `decimal.ConversionSyntax` |
| 18:30:51 | 1 | 0 | **GWEI/USDT stagnation exit** (30min, R=0.18, +1.24%) |
| 18:30:55 | 0 | 0 | **ACE/USDT stagnation exit** (30min, R=-0.62, -3.36%) |
| 18:31:59 | 0→1 | 0 | **ACE/USDT orphan synced** from Bybit → immediately regime shift killed |
| 18:32:10 | 1→0 | 0 | ACE/USDT force closed — regime_shift__to_RANGE |
| 18:32:14 | 0 | 0 | ⚠️ trade_memory store FAILED for ACE/USDT: `decimal.ConversionSyntax` |
| 18:35:41 | 0 | 0 | ⚠️ pybit timeout (HTTPSConnectionPool read timeout 10s) — auto-recovered |
| 19:00:10 | 0 | 0 | **KAITO/USDT rejected** — Low Score (54.0 < gate 97.5) |
| 19:00:30–50 | 0 | 6 | Shadow burst: SNXX, AKE, ACE, GWEI, SKHYNIX, SNDK entries |
| 19:10:35–51 | 0 | 0 | Shadow stagnation exits: all 5 positions (10min R=0.00) |
| 20:00:37–01:22 | 0 | 4 | Shadow burst: SKHYNIX, ESPORTS, TOWNS, ACE entries |

---

## 🔴 ANOMALY & BLOCKER REPORT

### CRITICAL: Phantom Trade Loop (TOWNS/USDT)

**Severity:** 🔴 CRITICAL
**Time:** 18:16:01 → 18:16:09 (8 seconds, 8 APM reconcile iterations)
**Symptom:** APM detected TOWNS/USDT as a phantom trade ("does not exist on Bybit") but continued looping instead of immediately closing it

**Evidence:**
```
18:16:01 CRITICAL APM reconcile: TOWNS/USDT is a phantom trade (does not exist on Bybit). Ignoring reconciliation.
18:16:02 CRITICAL APM reconcile: TOWNS/USDT is a phantom trade (does not exist on Bybit). Ignoring reconciliation.
18:16:04 CRITICAL APM reconcile: TOWNS/USDT is a phantom trade (does not exist on Bybit). Ignoring reconciliation.
18:16:07 CRITICAL APM reconcile: TOWNS/USDT is a phantom trade (does not exist on Bybit). Ignoring reconciliation.
18:16:09 CRITICAL APM reconcile: TOWNS/USDT is a phantom trade (does not exist on Bybit). Ignoring reconciliation.
... (8 total iterations)
18:16:09 exit detected: TOWNS/USDT LONG pnl=-0.37 reason=sl
```

**Root Cause:** When APM detects a phantom trade (position in Redis but not on Bybit), it logs CRITICAL but doesn't immediately remove the Redis key. It relies on the exit detection loop to clean up, causing 5–8 wasted reconcile iterations per phantom.

**Fix Required:** On phantom detection, immediately call `position_store.remove()` instead of waiting for exit detection.

---

### WARNING: Orphan Grace Period → Immediate Regime Kill (BANK/USDT, ACE/USDT)

**Severity:** ⚠️ WARNING
**Time:** 18:22:27 (BANK), 18:31:59 (ACE)
**Symptom:** Both positions were orphaned on Bybit (not in Redis), synced by APM's orphan detection, then immediately killed by regime shift detection within 13–15 seconds

**Timeline (BANK/USDT):**
```
18:21:28 APM: trade_memory stored BANK/USDT pnl=-0.93% reason=stagnation_exit_30min
18:22:27 APM: synced orphan BANK/USDT LONG from Bybit     ← re-discovered from exchange
18:22:28 APM reconcile: updated 8 fields                   ← fully reconstructed
18:22:40 APM: regime shift kill switch BANK/USDT → RANGE    ← 13 seconds later, killed
18:22:42 APM: force closed BANK/USDT — regime_shift__to_RANGE
18:22:43 APM: trade_memory store failed: decimal.ConversionSyntax  ← error on close
```

**Timeline (ACE/USDT):**
```
18:30:58 APM: trade_memory stored ACE/USDT pnl=-3.36% reason=stagnation_exit_30min
18:31:59 APM: synced orphan ACE/USDT LONG from Bybit      ← re-discovered from exchange
18:32:00 APM reconcile: updated 8 fields                   ← fully reconstructed
18:32:10 APM: regime shift kill switch ACE/USDT → RANGE     ← 10 seconds later, killed
18:32:11 CRITICAL APM reconcile: ACE/USDT is a phantom trade  ← now detected as phantom!
18:32:12 APM: force closed ACE/USDT — regime_shift__to_RANGE
18:32:14 APM: trade_memory store failed: decimal.ConversionSyntax  ← error on close
```

**Root Cause:** The orphan sync correctly pulls positions from Bybit, but the regime classifier immediately sees the new position as having entered in a now-RANGE regime (the market shifted during the orphan's lifetime). The regime shift kill switch fires within seconds of orphan adoption.

**Impact:** These were legitimate positions that got killed twice — once by stagnation exit (which only updated Redis, not Bybit), then by orphan sync + regime kill. The double-close creates phantom entries in trade_memory.

---

### WARNING: trade_memory Store Failure (decimal.ConversionSyntax)

**Severity:** ⚠️ WARNING
**Time:** 18:22:43 (BANK), 18:32:14 (ACE)
**Symptom:** `trade_memory.store()` fails with `decimal.ConversionSyntax` when recording regime-killed exits

**Root Cause:** The regime kill path passes a non-Decimal value (likely `None` or empty string) to a Decimal field in trade_memory. The stagnation exit path works correctly because it computes PnL from entry/exit prices.

**Fix Required:** Ensure regime shift kill path provides valid Decimal PnL before calling trade_memory.store().

---

### INFO: UniverseScanner DEBUG Log Spam

**Severity:** ℹ️ INFO (no functional impact)
**Count:** ~900+ lines in 784KB dataengine log
**Pattern:** `UniverseScanner.refresh:188` fires every ~300ms during scan cycles

**Impact:** Log noise only. No functional impact, but makes forensic analysis harder. Consider reducing log level for refresh() to TRACE or adding a rate limiter.

---

### INFO: pybit Transient Timeout

**Severity:** ℹ️ INFO (auto-recovered)
**Time:** 18:35:41
**Symptom:** `HTTPSConnectionPool(host='api.bybit.com'): Read timed out. (read timeout=10)`
**Recovery:** Single retry succeeded, no position impact

---

## 📊 ASM PARAMETER VALIDATION

| Parameter | Expected | Actual | Status |
|-----------|----------|--------|--------|
| Max live positions | 5 | 4 (start) → 0 (end) | ✅ Never exceeded |
| Max shadow positions | unbounded | 1–6 | ✅ Normal |
| Stagnation exit (live) | 30min | 3 exits @ 30min | ✅ Correct |
| Stagnation exit (shadow) | 10min | 6 exits @ 10min | ✅ Correct |
| Regime shift kill | 5 consecutive checks | 2 kills @ 5 checks | ✅ Threshold respected |
| Orphan detection | sync from Bybit | 2 orphans synced | ✅ Working |
| Phantom detection | detect + close | Detected but slow to close | ⚠️ 8-loop delay |

---

## 🔄 PARALLEL E2E TRACES

### Trace 1: GWEI/USDT (Live — Full Lifecycle)
```
18:04:01  [LIVE] Position open (pre-existing)
18:11:41  [APM]  Half-breakeven locked at 0.02507
18:30:51  [APM]  STAGNATION exit after 30min (R=0.18)
18:30:53  [APM]  Force closed — stagnation_exit_30min
18:30:55  [TM]   Stored pnl=+1.24%
✅ CLEAN LIFECYCLE — entry → breakeven → stagnation exit → recorded
```

### Trace 2: TOWNS/USDT (Live — Phantom Loop)
```
18:04:01  [LIVE] Position open (pre-existing)
18:16:01  [APM]  Phantom detected — "does not exist on Bybit"
18:16:01–09 [APM] 8 reconcile iterations (phantom loop!)
18:16:09  [EXIT] Exit detected — SL hit, pnl=-$0.37
18:16:09  [TM]   Stored pnl=-3.22%
⚠️ ANOMALY — 8 wasted reconcile iterations before cleanup
```

### Trace 3: BANK/USDT (Live — Orphan → Regime Kill → Store Fail)
```
18:21:23  [APM]  STAGNATION exit after 30min (R=-0.09)
18:21:26  [APM]  Force closed — stagnation_exit_30min
18:21:28  [TM]   Stored pnl=-0.93%
18:22:27  [APM]  Orphan synced from Bybit (position still live on exchange!)
18:22:28  [APM]  Reconciled — 8 fields updated
18:22:40  [APM]  Regime shift kill → RANGE (5 checks)
18:22:42  [APM]  Force closed — regime_shift__to_RANGE
18:22:43  [TM]   ❌ FAILED: decimal.ConversionSyntax
⚠️ DOUBLE LIFECYCLE — position killed twice, second trade_memory write failed
```

### Trace 4: Shadow Burst (19:00–19:10)
```
19:00:35  [SHADOW] SNXX/USDT SHORT entry
19:00:41  [SHADOW] AKE/USDT LONG entry
19:00:43  [SHADOW] ACE/USDT LONG entry
19:00:43  [SHADOW] GWEI/USDT LONG entry
19:00:46  [SHADOW] SKHYNIX/USDT LONG entry
19:00:50  [SHADOW] SNDK/USDT SHORT entry
19:10:35  [APM]   All 6 stagnation exits at 10min (R=0.00)
⚠️ ALL POSITIONS EXITED AT R=0.00 — no price movement captured in 10min window
```

---

## 🎯 KEY FINDINGS & RECOMMENDATIONS

### ✅ What's Working
1. **Stagnation exits** — both live (30min) and shadow (10min) firing correctly
2. **Position cap** — never exceeded max 5
3. **Orphan detection** — correctly finds positions on Bybit not in Redis
4. **Regime shift kill** — correctly kills positions when regime changes (5-check threshold)
5. **AI Analyst** — all signals passed (pre-Soft-Gate era, no FLAT penalty yet)
6. **VPN/DNS** — single transient timeout, auto-recovered

### 🔴 Critical Fixes Needed
1. **Phantom trade immediate cleanup** — On phantom detection, remove Redis key immediately instead of looping 5–8 times
2. **trade_memory decimal error** — Ensure regime kill path provides valid Decimal PnL
3. **Orphan → regime kill race** — Add a grace period (e.g., 60s) after orphan sync before regime kill fires, to avoid killing immediately-recovered positions

### ⚠️ Improvements
4. **UniverseScanner log level** — Reduce from DEBUG to TRACE to cut ~900 log lines
5. **Shadow stagnation exit timing** — All 6 shadow positions exited at R=0.00 — consider extending shadow hold time beyond 10min for better signal quality data

---

## 📋 COMPARISON: PRE-SOFT-GATE vs POST-SOFT-GATE

This capture represents the **baseline before Soft Gate deployment**. Key differences observed after Soft Gate:

| Metric | This Capture (Pre) | Post-Soft-Gate |
|--------|-------------------|----------------|
| AI Analyst rejections | 0 (all passed) | 0 (FLAT → light penalty) |
| Shadow executions in 1h | 6 (burst at 19:00) | 7 (distributed) |
| Live executions in 1h | 0 new (only orphan syncs) | 5 new entries |
| Phantom trades | 1 (TOWNS/USDT) | 0 (fixed in later builds) |
| Regime DISABLE blocks | 0 | ~50+ (until config fix) |
| Moon bag errors | N/A | 1 (create_order → reduce_position fix) |

---

*Report generated: 2026-07-25*
*Capture: monitoring/forensic_2h_20260725_0101/*
