# Definition of Done (DoD)
**Project Name:** `karsa-auto-session-manager`  
**Document Status:** Approved / Locked _(rev. 2 — added Shadow Mode 3.L checklist, WARP -> gluetun)_  
**Purpose:** Establish the strict, non-negotiable quality gates that every feature, module, or bugfix must pass before it is merged into the `main` branch and deployed to the live paper-trading environment.

---

## 1. The Core Philosophy
> **"In a trading system, 'done' means 'safe'. If it works but lacks telemetry, it is not done. If it executes but lacks error handling for proxy drops, it is not done. If it makes money in a backtest but uses `float` instead of `Decimal`, it is not done."**

Every pull request (PR) must explicitly check off the relevant boxes in this document. 

---

## 2. The 5 Universal Pillars of "Done"

Regardless of which component is being built, it must satisfy these five universal criteria:

### Pillar 1: Code Quality & Strict Typing
- [ ] **Linting & Formatting:** Code passes `ruff check` and `black` formatting with zero warnings.
- [ ] **Strict Typing:** Code passes `mypy --strict`. No use of `Any` unless absolutely unavoidable (and documented).
- [ ] **Financial Math Rule:** **Zero use of `float` for prices, sizes, or PnL.** All financial calculations must use `decimal.Decimal`.
- [ ] **Asyncio Compliance:** No blocking synchronous calls (e.g., `time.sleep()`, standard `requests`) inside the `asyncio` event loop. All I/O must be `await`ed.

### Pillar 2: Unit Testing (Math & Logic)
- [ ] **Coverage:** Core logic has > 90% unit test coverage using `pytest`.
- [ ] **Math Validation:** Alpha calculations (e.g., Global Skew, VWAP) are tested against static, hand-calculated JSON inputs.
- [ ] **Edge Cases:** Tests explicitly cover edge cases: divide-by-zero, missing exchange data, extreme outlier "bad ticks", and empty order books.
- [ ] **Pydantic Validation:** Tests verify that invalid data payloads correctly raise `pydantic.ValidationError` before entering the core logic.

### Pillar 3: Integration & Execution Testing
- [ ] **Live Verification (Bybit Main URL):** Any execution logic must be successfully tested against **live Bybit** (main URL). Testnet is not accessible — use the $1 max-loss-per-position SL hard cap and a micro position size as the safety boundary. Never rely on testnet.
- [ ] **Proxy Verification:** Integration tests must confirm that traffic is successfully routing through the WireGuard VPN via gluetun without authentication errors.
- [ ] **WebSocket Resilience:** Data and Execution WebSockets must pass a "choke test" (simulating network drops) and prove they auto-reconnect and reconcile state without crashing the bot.

### Pillar 4: Observability & Telemetry
- [ ] **Prometheus Metrics:** New features must expose relevant metrics via `prometheus-client` (e.g., `karsa_alpha_signals_generated_total`, `karsa_bybit_order_latency_seconds`).
- [ ] **Postgres Logging:** All state changes, signals, and trades must be written to the correct PostgreSQL tables (`signals`, `trades`, `system_events`) matching the exact `JSONB` schemas defined in `DATA_MODEL.md`.
- [ ] **Structured Logging:** All `print()` statements are replaced with structured `loguru` or `logging` calls, including contextual metadata (e.g., `symbol`, `signal_id`).

### Pillar 5: Safety & Risk Verification
- [ ] **Circuit Breaker Compliance:** The new feature must gracefully halt or degrade if the global Circuit Breaker is triggered.
- [ ] **Kill Switch Compatibility:** The feature must not block the main `asyncio` loop, ensuring the Telegram/File Kill Switch can interrupt it instantly.
- [ ] **State Reconciliation:** If the feature alters position state, it must be verified by the Startup Reconciliation Engine.

---

## 3. Component-Specific DoD Checklists

When working on specific modules, the following additional criteria apply:

### A. Global Data Engine (Key 1)
- [ ] Handles disparate exchange schemas (Binance vs OKX) via the Normalizer.
- [ ] "Bad Tick" filter successfully rejects price spikes > 5% in < 1 second.
- [ ] Marks an exchange as `STALE` if WebSocket heartbeat exceeds 15 seconds.

### B. Alpha Bridge (Key 2)
- [ ] Calculates metrics using only `ACTIVE` exchange data (ignores `STALE` exchanges).
- [ ] Outputs a strictly typed `TradingSignal` Pydantic model.
- [ ] Does not block the event loop for > 5ms during calculation.

### C. 3-Layer Risk Gate (Key 3)
- [ ] Evaluates all 3 gates (Liquidity, Spread Health, Circuit Breaker) sequentially.
- [ ] Logs the exact `RiskDecision` (pass/fail and reason) to the `signals` Postgres table.
- [ ] Hard-stops the bot immediately if the Daily Drawdown gate is tripped.

### D. Bybit Executor (Key 4)
- [ ] Uses Bybit **Private WebSockets** for order management, not REST (where applicable).
- [ ] Implements the 3-step SOR: Post-Only Limit $\rightarrow$ Reprice $\rightarrow$ Market/IOC.
- [ ] **Mandatory:** Places an exchange-side Stop-Loss immediately upon position fill.
- [ ] Handles partial fills correctly without duplicating orders.

### E. Watchdog & Telemetry (Key 6)
- [ ] Successfully pings the external "Dead Man's Switch" every 60 seconds.
- [ ] Accurately tracks and exposes execution latency to Prometheus.
- [ ] Triggers a graceful shutdown (flatten $\rightarrow$ cancel $\rightarrow$ exit) on `SIGINT`/`SIGTERM`.

### F. Universe Scorer (Stage 1)
- [ ] Scoring formula unit tests with hand-calculated fixtures (Volume + Momentum + Squeeze + Overextension).
- [ ] Sector mapping covers all configured symbols (no unclassified symbols).
- [ ] Empty universe graceful degradation: falls back to static symbol list from config.
- [ ] Redis write/read round-trip test for `system:universe:symbols`.
- [ ] Sector cap enforcement: rejects signals when sector already at max positions.

### G. AI CryptoAnalyst (Stage 3, MANDATORY)
- [ ] 9router client handles retry, timeout (15s), and network errors.
- [ ] Prompt template versioned in code (changes are reviewable).
- [ ] JSON parse failure $\rightarrow$ signal rejection (never bypass AI on parse error).
- [ ] Confidence blend formula unit test: `quant * 0.5 + AI * 0.5`.
- [ ] Redis cache TTL verification (5min, key = `ai:cache:{hash}`).
- [ ] AI unavailable $\rightarrow$ signal rejected (mandatory means mandatory).

### H. AI Position Judge (Stage 6, MANDATORY in ambiguous zone)
- [ ] 2-tier escalation path tested: cheap (haiku) $\rightarrow$ escalated (sonnet).
- [ ] 3 consecutive HOLDs on losing position $\rightarrow$ forced EXIT test.
- [ ] Parse failure $\rightarrow$ conservative HOLD (fail-safe, never exit without AI).
- [ ] AI unavailable $\rightarrow$ HOLD (don't exit without AI verdict).
- [ ] `JudgeVerdict` Pydantic model validation.

### I. Multi-Timeframe Filter
- [ ] 4H EMA(20) computation test against known candle data.
- [ ] Contradiction penalty (0.5x) applied when 1H signal opposes 4H trend.
- [ ] Graceful degradation: no penalty when 4H OHLCV unavailable.

### J. Trade Memory
- [ ] Redis sorted set write/read test for `karsa:memory:{symbol}`.
- [ ] Prompt formatting test: last 3 trades matching symbol + regime.
- [ ] Memory injection into AI analyst prompt verified.

### K. Executor Task Wiring
- [x] `sor.execute()` is called from `executor_task` (not just reconcile).
- [x] Position store registration after fill (`position_store.save()`).
- [x] Duplicate position prevention via `position_store.has_position()`.
- [ ] Circuit breaker updated on trade results (`update_pnl`, `record_loss`).

### L. Shadow Mode (Phase 3.1)

- [ ] `ShadowExecutor.execute()` correctly routes to maker vs taker fee based on `is_post_only` parameter
- [ ] `ShadowAPM` tracks `worst_price_seen` and triggers SL on wick hits (not just current price at polling interval)
- [ ] `ShadowAPM` deducts 8-hour funding rate drag on held positions
- [ ] Pending limit orders transition from `PENDING_VIRTUAL_FILL` to `OPEN` when live price crosses virtual entry in the correct direction
- [ ] Shadow positions use `shadow:position:*` Redis namespace — zero collision with live `position:*` keys
- [ ] Shadow trades write to `shadow_trades` table — zero writes to `trades` table
- [ ] Startup reconciliation is explicitly skipped when `SHADOW_MODE_ENABLED=true`
- [ ] Position reconciler task is not started when `SHADOW_MODE_ENABLED=true`
- [ ] Switching from shadow to live mode does not leave orphaned shadow state that interferes with live operation
- [ ] Unit tests exist for fee asymmetry (maker vs taker), wick detection (worst_price_seen), funding deduction, and pending limit state machine

---

## 4. The "Definition of NOT Done" (Anti-Patterns)

A PR will be **immediately rejected** if it contains any of the following:

1. ❌ **Using `float` for money:** `price = 64000.50` instead of `Decimal("64000.50")`.
2. ❌ **Hardcoded Secrets:** API keys, Telegram tokens, or DB passwords in the code. (Must use `.env` via Pydantic `Settings`).
3. ❌ **Silent Failures:** `try...except` blocks that catch exceptions and do nothing (`pass`), or fail to log the error to Postgres/Telegram.
4. ❌ **Blocking the Loop:** Using `time.sleep(1)` instead of `await asyncio.sleep(1)`.
5. ❌ **Missing Exchange-Side SL:** Opening a position without immediately placing a hard Stop-Loss on the Bybit server.
6. ❌ **Guessing Field Names:** Using arbitrary dictionary keys (`data['price']`) instead of strict Pydantic models.

---

## 5. The Sign-Off Process

For a feature to be merged into `main`:
1. Developer checks off all applicable boxes in this DoD.
2. `pytest` passes locally with 0 failures.
3. `ruff`, `black`, and `mypy` pass with 0 errors.
4. The bot is run locally in Docker for at least **15 minutes** to ensure no immediate memory leaks or WebSocket drops.
5. (Optional for critical modules) Code review by a second pair of eyes (or rigorous AI-assisted review using `CLAUDE.md`).

***