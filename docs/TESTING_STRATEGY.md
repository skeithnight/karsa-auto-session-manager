# Testing Strategy
**Project Name:** `karsa-auto-session-manager`
**Document Status:** Draft — Proposed (derived from `DEFINITION_OF_DONE.md`, `RISK_AND_RUNBOOK.md`, `ARCHITECTURE.md`)
**Purpose:** Define exactly what gets tested, how, with what tools, and at what stage of the pipeline — so "passes DoD" is a checkable fact, not a feeling.

---

## 1. Philosophy

`DEFINITION_OF_DONE.md` states it plainly: *"done" means "safe."* This strategy exists to make "safe" falsifiable. Every safety claim in `RISK_AND_RUNBOOK.md` (kill switch, circuit breakers, reconciliation, proxy failover) must have a corresponding automated test that fails loudly if the behavior regresses. If a safety behavior has no test, it does not exist.

Three failure modes this strategy is designed to catch before production:
1. **Silent math errors** — `float` drift, wrong Skew/VWAP formulas, bad Decimal rounding.
2. **Silent state divergence** — DB says one thing, Bybit says another, and nothing notices.
3. **Silent degradation** — proxy slows down, WS goes stale, and the bot keeps trading anyway.

---

## 2. Test Pyramid & Environment Matrix

| Level | Scope | Tools | Environment | Speed | Runs |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Unit** | Pure functions: math, normalizers, filters, Pydantic models | `pytest`, `pytest-asyncio`, `hypothesis` | None (in-process) | ms | Every commit |
| **Integration** | Component-to-component: DB writes, proxy routing, WS reconnect | `pytest`, `testcontainers-python` (Postgres), mock WS servers | Docker (ephemeral) | seconds–minutes | Every PR |
| **Execution/Live** | Real order lifecycle on exchange | `pytest` + live Bybit SDK calls (micro size + $1 SL cap) | **Bybit Main URL via WARP** | minutes | Pre-merge to `main`, nightly |
| **Chaos/Safety** | Kill switch, circuit breakers, proxy failover, reconciliation scenarios | Custom fault-injection harness | Docker + live Bybit (mocked where safe) | minutes | Pre-merge to `main`, nightly |
| **Soak** | Long-running stability | Manual/scheduled run + Prometheus assertions | Full Docker stack, live Bybit | 15 min – 14 days | Pre-release gates (see §6) |

---

## 3. Unit Testing (DoD Pillar 2)

**Target: >90% coverage on `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, `app/data/filters/py`, and all Pydantic models.**

- **Decimal discipline:** `hypothesis`-based property tests assert that no function under test ever accepts or returns a `float` where a `Decimal` is expected. A `conftest.py` fixture can monkeypatch `Decimal.__float__` calls in test mode to flag accidental coercion.
- **Alpha math validation:** Global VWAP, Global Skew, and Lead-Lag calculations are tested against static, hand-calculated JSON fixtures (`tests/fixtures/alpha/*.json`) per `DEFINITION_OF_DONE.md` §2. Each fixture pairs raw multi-exchange input with a pre-computed expected output, checked into version control so the "correct answer" is never trusted to the code under test.
- **Edge cases (mandatory, from DoD):** divide-by-zero (zero volume), missing exchange data (one exchange absent from the payload), extreme outlier bad ticks (>5% in <1s), empty order books, all-`STALE` exchange set.
- **Pydantic validation:** every model in `DATA_MODEL.md` §4 gets a test asserting malformed input raises `pydantic.ValidationError` *before* it reaches core logic — not just "happy path" construction tests.
- **Bad Tick Filter:** parametrized tests across spike magnitudes (4.9%, 5.0%, 5.1%) and timing windows to pin the exact boundary behavior.

---

## 4. Integration Testing (DoD Pillar 3)

- **Postgres schema conformance:** `testcontainers` spins an ephemeral Postgres 15 instance per test run; migrations apply the exact DDL from `DATA_MODEL.md` §3. Tests insert real `TradeExecution`/`TradingSignal`/`system_events` objects and assert the round-tripped row matches field-for-field, including `JSONB` snapshot shape.
- **WARP Proxy Verification:** an integration test confirms outbound Bybit traffic actually routes through `socks5h://host.docker.internal:1080` (e.g., asserting egress IP differs from host IP, or asserting connection fails cleanly when the proxy is intentionally down — never silently falls back to direct connection).
- **WebSocket Resilience ("Choke Test"):** a mock WS server (or `toxiproxy`) intentionally severs Binance/OKX/Bybit connections mid-stream. Assertions: (1) reconnect happens automatically, (2) the exchange is marked `STALE` within the 15s window from `DEFINITION_OF_DONE.md` §3.A, (3) no unhandled exception crashes the event loop.
- **Live Bybit Execution (Main URL):** place/cancel/verify small real orders on **live Bybit main URL** with the $1 max-loss-per-position SL hard cap as the safety boundary. Assert the full SOR sequence (Post-Only Limit → Reprice → Market/IOC) executes in order, and that an exchange-side Stop-Loss is placed **atomically with** (not after, not optionally) every fill — this is the single most safety-critical assertion in the suite per DoD anti-pattern #5. **Note:** Testnet is not accessible — live Bybit with micro-size is the only execution test environment.
- **Idempotent Execution:** simulate a crash immediately after an order is sent but before the fill is persisted, then restart the reconciliation flow. Assert exactly one order exists on the exchange — never a duplicate.

---

## 5. Safety & Chaos Testing (DoD Pillar 5 + `RISK_AND_RUNBOOK.md`)

Each row below is a required automated test, not a manual QA pass.

| Runbook Behavior | Test Name (suggested) | Core Assertion |
| :--- | :--- | :--- |
| Kill Switch (Telegram) | `test_kill_switch_telegram_trigger` | Full sequence (cancel-all → flatten → halt → alert → exit) completes in **< 10s** |
| Kill Switch (file flag) | `test_kill_switch_file_flag_trigger` | Same as above, triggered via `/tmp/KILL_KARSA` |
| Daily Drawdown circuit breaker | `test_circuit_breaker_drawdown_hard_stop` | Bot flattens + halts at the threshold — **see Open Issue #2 in `CONTEXT.md`, threshold value is ambiguous across docs and must be resolved before this test is written** |
| Consecutive Losses (soft) | `test_soft_stop_three_losses` | New trade generation paused 60 min; existing positions still managed |
| Execution Latency Spike | `test_latency_spike_halts_new_entries` | >1500ms rolling avg → cancels open orders, pauses entries, fires Telegram alert |
| Margin Utilization | `test_margin_halt_at_40pct` | Blocks new opens; existing positions still manageable |
| Stale Data | `test_stale_data_halts_alpha` | >15s no WS update → Alpha Bridge paused, no new trades |
| Proxy Degradation (>2000ms) | `test_proxy_failover_does_not_market_flatten` | Confirms it **cancels limit orders only** and does **not** attempt a market flatten through a degraded proxy — this is the highest-risk anti-pattern in the runbook and deserves its own explicit negative test |
| Proxy Auto-Resume | `test_proxy_resume_after_60s_stable` | Trading resumes only after 60s of stability |
| Reconciliation — Scenario A (Clean) | `test_reconcile_clean_match` | No-op, proceeds to normal startup |
| Reconciliation — Scenario B (Orphaned Orders) | `test_reconcile_cancels_orphaned_orders` | Bybit-only orders get cancelled |
| Reconciliation — Scenario C (Ghost Positions) | `test_reconcile_overwrites_with_exchange_truth` | Postgres overwritten by Bybit truth; `CRITICAL` event logged |
| Reconciliation — Scenario D (Postgres Dead) | `test_reconcile_rebuilds_schema_on_db_failure` | Fresh schema created and populated from Bybit; bot still starts |
| Dead Man's Switch | `test_dead_mans_switch_ping_cadence` | Ping fires every 60s; simulated 3-minute silence triggers external alert path |
| Event Loop Lag | `test_watchdog_detects_loop_lag` | Injected >100ms blocking call triggers graceful shutdown + flatten |
| Graceful Shutdown (SIGTERM/SIGINT) | `test_sigterm_runs_kill_sequence` | Signal handlers run the full Kill Sequence before process exit — never a bare `sys.exit` |

---

## 6. Soak / Longevity Testing

| Gate | Duration | Environment | Pass Criteria (from `MVP_SCOPE.md` / `DEFINITION_OF_DONE.md`) |
| :--- | :--- | :--- | :--- |
| Pre-merge smoke | 15 min | Local Docker | No memory leak, no WS drop, no unhandled exception |
| Phase 4 integration soak | 72 hrs | **Live Bybit main URL** (micro-size, $1 SL cap) | Zero crashes, zero state divergences, all trades logged correctly |
| MVP graduation soak | 14 days | **Live Bybit main URL** | Zero unhandled exceptions/divergence, avg latency <800ms, win rate >50% with R:R >1.2, circuit breaker intentionally tripped once and verified to flatten correctly |

Soak runs should assert against Prometheus metrics directly (e.g., scrape `karsa_bybit_order_latency_seconds` p95 continuously) rather than relying on end-of-run manual log review.

---

## 7. CI/CD Test Gates

| Stage | Trigger | Gate |
| :--- | :--- | :--- |
| Pre-commit | Local `git commit` | `ruff`, `black --check`, `mypy --strict` |
| PR opened/updated | GitHub Actions | Unit tests + coverage >90% on core logic paths |
| Merge to `main` | Post-approval merge | Integration suite (testcontainers Postgres, WS choke test, proxy verification) |
| Nightly | Scheduled | Live Bybit execution suite (micro-size) + full Chaos/Safety table (§5) |
| Pre-release (V1.0 → V1.1) | Manual gate | 14-day soak results reviewed against MVP Success Criteria |

A PR cannot merge if any Chaos/Safety test (§5) is skipped, `xfail`, or commented out — these are the tests most likely to be quietly disabled under deadline pressure, and they're precisely the ones protecting capital.

---

## 8. Fixture & Test Data Strategy

```text
tests/
├── conftest.py                     # shared fixtures: db container, mock WS server, frozen clock
├── fixtures/
│   ├── alpha/                      # static hand-calculated VWAP/Skew JSON cases
│   ├── ws_payloads/                # recorded raw Binance/OKX/Bybit payloads for normalizer tests
│   └── reconciliation/             # Bybit REST response fixtures for Scenario A-D
├── unit/
│   ├── test_alpha_metrics.py
│   ├── test_alpha_signals.py
│   ├── test_regime_classifier.py    # Hurst + ADX unit tests (Phase 1)
│   ├── test_ohlcv_fetcher.py        # Cache TTL, error handling (Phase 1)
│   ├── test_lead_lag_buffer.py      # Rolling window math (Phase 2)
│   ├── test_signal_composite.py     # Multi-signal combinations (Phase 2)
│   ├── test_entry_filter.py         # All 5 filter conditions (Phase 3)
│   ├── test_trailing_stop.py        # ATR stop calculation (Phase 4)
│   ├── test_checkpoint_manager.py   # Zone classifications (Phase 4)
│   ├── test_normalizer.py
│   ├── test_filters.py
│   ├── test_risk_gates.py
│   ├── test_sor.py
│   └── test_config.py
├── integration/
│   ├── test_postgres_schema.py
│   ├── test_warp_proxy.py
│   ├── test_ws_choke.py
│   └── test_testnet_execution.py
└── chaos/
    ├── test_kill_switch.py
    ├── test_circuit_breakers.py
    ├── test_proxy_failover.py
    ├── test_reconciliation_scenarios.py
    └── test_watchdog.py
```

Additional test files for 6-stage lifecycle:

```text
tests/unit/
├── test_universe_scorer.py       # Scoring formula, sector cap, empty universe fallback
├── test_sector_mapping.py        # All symbols classified, no unknowns
├── test_sector_cap.py            # Cap enforcement, counting logic
├── test_multi_tf.py              # 4H EMA, contradiction penalty, graceful degradation
├── test_trade_memory.py          # Redis sorted set write/read, prompt formatting
├── test_analyst.py               # Mock 9router, confidence blend, parse failure rejection
├── test_position_judge.py        # 2-tier escalation, 3-HOLD exit, fail-safe
└── test_executor_wiring.py       # Mock SOR, verify sor.execute() called, position store registration

tests/integration/
└── test_full_lifecycle.py        # End-to-end: mock exchanges → universe → regime → signal+AI → risk → executor → position lifecycle → exit
```

Use `pytest.mark.integration`, `pytest.mark.testnet`, `pytest.mark.chaos`, and `pytest.mark.soak` markers so CI stages can select subsets without maintaining separate test runners.

### AI Testing Strategy

AI components must be tested with **mocked 9router** — never call real Anthropic API in tests.

- **Mock HTTP client:** Patch `AIClient.complete()` to return fixed JSON responses.
- **Deterministic behavior:** Test confidence blend formula with known AI outputs.
- **Failure modes to test:**
  - 9router timeout (15s) → signal rejected (AI mandatory)
  - Bad JSON response → signal rejected
  - Empty response → signal rejected
  - Network error → signal rejected
  - AI returns FLAT when deterministic says LONG → signal rejected (blend drops below threshold)
- **Edge cases:**
  - AI confidence = 0 → `quant * 0.5 + 0 * 0.5` = half of deterministic
  - AI confidence = 100 → maximum boost
  - Position judge returns HOLD 3 times → forced EXIT
  - Position judge returns EXIT on first call → immediate exit

---

## 9. Traceability: DoD Anti-Patterns → Enforcement

| Anti-Pattern (`DEFINITION_OF_DONE.md` §4) | Enforcement Mechanism |
| :--- | :--- |
| `float` for money | `mypy --strict` + `hypothesis` type-fuzz tests in §3 |
| Hardcoded secrets | Pre-commit secret scanner (e.g., `detect-secrets`) + `test_settings_from_env_only.py` |
| Silent `except: pass` | `ruff` rule `BLE001`/`S110` + code review checklist |
| Blocking the loop | `test_watchdog_detects_loop_lag`, plus `ruff` async-blocking lint rules |
| Missing exchange-side SL | `test_testnet_execution.py::test_sl_placed_atomically_with_fill` |
| Guessing field names | `mypy --strict` on all Pydantic models; no raw dict access permitted outside `normalizer.py` |

---

## 10. Open Testing Risks / Unknowns

- **Redis is confirmed in scope** (see `CONTEXT.md` Issue #1 — resolved). Redis is already used for 7+ keys. `testcontainers-redis` integration tests needed for `GlobalStateCache`, `system:heartbeat`, `system:circuit_breaker`, and `position_store` round-trips.
- **Circuit breaker drawdown threshold** — code uses 2% (`Decimal("-0.02")`). This is the authoritative value. Tests should assert against 2%.
- **Exchange-side SL** — ✅ Implemented (`app/execution/bybit_client.py`). Phase 0B complete.
- **Regime classification** — ✅ Implemented (`app/alpha/regime.py`). Hurst + ADX + EMA200 on BTC 1H. Phase 1 complete.