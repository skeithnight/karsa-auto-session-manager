### 🗺️ Enhancement Roadmap Overview

| Phase | Focus Area | Expected Impact | Timeframe |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Critical Stabilization** (Bug Fixes) | Eliminate phantom loops, false-positive exits, and startup failures. | Immediate (1-2 Weeks) |
| **Phase 2** | **Performance & Hot-Path Optimization** | Reduce latency, eliminate blocking AI calls, optimize Redis/DB I/O. | Short-term (2-3 Weeks) |
| **Phase 3** | **Advanced Alpha & Risk Refinement** | Smarter regime detection, dynamic correlation, improved ML prefilter. | Mid-term (3-4 Weeks) |
| **Phase 4** | **Observability & DevEx** | Full metric coverage, structured logging, automated alerting. | Ongoing |

---

### 🔧 Phase 1: Critical Stabilization (The "Must-Fix" Debt)

*These address the 🔴 P0 and 🟡 P1 issues identified in the forensic reports.*

1. **State Machine Cooldowns for Position Management**
   - **Enhancement**: ✅ **IMPLEMENTED** — `_recently_force_closed` dict with `ORPHAN_RE_ENTRY_GRACE_S = 60` added to `ActivePositionManager.__init__()` and guarded in orphan sync path. Timestamps recorded in `_force_close_position()` for both successful close and "already closed" (110017) cases.
   - **Why**: Breaks the orphan sync → force close → re-sync infinite loop definitively.
2. **Robust Cold-Start Bootstrapper**
   - **Enhancement**: ✅ **IMPLEMENTED** — Bootstrap phase added to `live_loop.py` after candle pre-fill. Feeds pre-filled candles to `MarketAnalyzer.update_on_candle_close()` so regime classification is available immediately. Logs bootstrap status (% of universe ready) and warns if <80% ready. Eliminates the 30-60min cold-start degradation window.
   - **Why**: Prevents degraded regime classification and false signals during the first 30–60 minutes of runtime.
3. **Resilient AI Gate with Exponential Backoff**
   - **Enhancement**: ✅ **IMPLEMENTED** — Retry loop with exponential backoff (1s, 2s) added to `CryptoAnalyst.analyze()`. Live mode retries up to 3 total attempts on transient failures. Shadow mode skips retries entirely (fail-open, immediate pass-through). `is_shadow` parameter added to `CryptoAnalyst.__init__()` with backward-compatible default `False`. Updated all 3 instantiation sites: `live_loop.py` (×2), `shadow_loop.py` (×1), `main.py` (×1).
   - **Why**: Survives transient 9router/API credential warm-up delays without immediately fail-closing valid live signals.
4. **Data Sanitization Layer**
   - **Enhancement**: Introduce a `_safe_decimal()` utility across `TradeStore` and `PositionStore` to handle `""`, `None`, or `"null"` strings from exchange APIs before casting to `Decimal` or `float`.
   - **Why**: Prevents the `ValueError: could not convert string to float` that corrupts trade memory and ML training data.

---

### ⚡ Phase 2: Performance & Hot-Path Optimization

*The current hot path is solid but has latent bottlenecks, particularly around the 30-second AI gate.*

1. **Decouple AI Analysis from the Synchronous Hot Path**
   - **Current State**: `CryptoAnalyst.analyze()` blocks the signal evaluation for ~30s.
   - **Enhancement**: Fire the AI request asynchronously (`asyncio.create_task`) and store the `TradeSignal` in a `pending_ai_review` Redis queue. The `DecisionEngine` can yield a "PENDING" state, allowing the main loop to process the next candle or manage existing positions without stalling. Once the AI returns, a separate listener evaluates the final approval.
   - **Why**: Crypto moves fast. A 30s block means missing the optimal entry price or failing to react to a sudden regime shift.
2. **Batched Redis/PostgreSQL Writes**
   - **Enhancement**: Instead of writing every candle or trade individually, implement a micro-batching mechanism (e.g., `asyncpg` `executemany` or Redis `pipeline`) for non-critical telemetry (e.g., `MarketDataIngestor` updates).
   - **Why**: Reduces I/O overhead and network chatter, freeing up the asyncio event loop for execution logic.
3. **Graceful Shutdown Hook Enforcement**
   - **Enhancement**: Ensure `SIGTERM` handlers explicitly call `await exchange.close()` for all CCXT instances and `await redis.close()`.
   - **Why**: Eliminates the "Unclosed client session" warnings and prevents file descriptor leaks over long-running deployments.

---

### 🧠 Phase 3: Advanced Alpha & Risk Refinement

*Enhancing the intelligence of the system to improve win rate and reduce drawdowns.*

1. **Regime Shift Hysteresis Upgrade**
   - **Current State**: Triggers on 3 consecutive checks, even if shifting from RANGE to RANGE (noise).
   - **Enhancement**: ✅ **PARTIALLY IMPLEMENTED**
     - ✅ `REGIME_SHIFT_CONFIRM_COUNT` increased from 3 to 5.
     - ✅ **Regime family guard** added via `REGIME_FAMILY` mapping. Shifts within the same family (e.g., `RANGE` → `RANGE_LOW_VOL`, `TREND_BULL` → `TREND_BEAR`) are treated as noise and reset the hysteresis counter. Only cross-family shifts (e.g., `RANGE` → `TREND`, `TREND` → `CHOP`) increment toward the kill threshold.
     - ⏳ Volatility filter (ATR threshold) — still pending.
2. **Dynamic Correlation Matrix**
   - **Current State**: Hard limit of "max 2 positions per sector".
   - **Enhancement**: Calculate a rolling 24h Pearson correlation coefficient between all open positions. If the correlation between two open positions exceeds `0.75`, dynamically reduce the position size of the lower-conviction trade by 50%, or block the new entry entirely.
   - **Why**: Prevents hidden concentration risk (e.g., being long on 3 different AI coins that all dump together).
3. **Universe Scanner Validation**
   - **Enhancement**: ✅ **IMPLEMENTED** — Added `market.get("active", True)` check to `DynamicUniverseScanner.refresh()`. Delisted/suspended symbols (e.g., `ESPORTS/USDT`) are now filtered out. Default `True` ensures backward compatibility with exchanges that don't set the `active` field.
   - **Why**: Stops the system from wasting API rate limits and generating signals for delisted or inactive pairs (like `ESPORTS/USDT`).

---

### 📊 Phase 4: Observability & Developer Experience (DevEx)

*You cannot improve what you cannot measure. The forensic reports were great, but they required manual log parsing.*

1. **Structured JSON Logging**
   - **Enhancement**: Replace standard `logging.info` with `structlog` or Python's `logging` configured for JSON output. Include `trace_id`, `symbol`, `regime`, and `action` in every log.
   - **Why**: Enables easy parsing by tools like Loki, Datadog, or even simple `jq` filters in the terminal.
2. **Prometheus Metrics for Gate Rejections**
   - **Enhancement**: Add specific counters for *why* a signal was rejected:
     - `karsa_signal_rejected_total{reason="ai_404"}`
     - `karsa_signal_rejected_total{reason="regime_hard_block"}`
     - `karsa_signal_rejected_total{reason="ml_prefilter"}`
   - **Why**: Allows you to look at a Grafana dashboard and instantly see if the AI gate is being too aggressive, or if the ML prefilter is broken.
3. **Automated Health Checks & Alerting**
   - **Enhancement**: Add a `/health` endpoint to the `live` container that checks:
     1. Redis connectivity.
     2. Postgres connectivity.
     3. Last candle timestamp < 5 minutes ago.
     4. AI provider status.
   - **Why**: Allows Docker or an external monitor (like Uptime Kuma) to auto-restart the container if the data pipeline silently stalls, rather than waiting for a manual check.

---

### 🎯 Expected ROI of These Enhancements

| Metric                     | Current State                               | Post-Enhancement Target                   |
| :---------------------------| :--------------------------------------------| :------------------------------------------|
| **Phantom Trade Loops**    | ~5x per event, causing confusion/minor fees | **0** ✅ (60s grace period blocks re-sync) |
| **False-Positive Exits**   | ~1 per 30 mins (RANGE→RANGE noise)          | **~0** ✅ (Family guard + 5-check hysteresis) |
| **Cold-Start Degradation** | 30–60 mins of poor regime classification    | **< 2 mins** (Formal Bootstrapper phase)  |
| **AI Gate Blocking**       | 30s synchronous block per signal            | **Asynchronous / Non-blocking**           |
| **Observability**          | Manual log grepping                         | **Real-time Grafana dashboards + Alerts** |

### 📝 Implementation Log (2026-07-24)

| Fix | Status | File | Lines Changed |
|:----|:-------|:-----|:-------------|
| `REGIME_SHIFT_CONFIRM_COUNT` 3→5 | ✅ Done | `position_manager.py` | L35 |
| `REGIME_FAMILY` guard (same-family = noise) | ✅ Done | `position_manager.py` | L37-48 |
| `_recently_force_closed` + 60s grace period | ✅ Done | `position_manager.py` | L67, L115-122, L1289-1290, L1296 |
| AI backoff with shadow/live asymmetry | ✅ Done | `analyst.py`, `live_loop.py`, `shadow_loop.py`, `main.py` | analyst.py L112-121, L243-276; live_loop.py ×2; shadow_loop.py ×1; main.py ×1 |
| Cold-start bootstrapper (partial universe) | ✅ Done | `live_loop.py` | L1283-1310 |
| Universe scanner `active` market check | ✅ Done | `universe_scanner.py` | L157 |
