# AGENTS.md
**Project:** `karsa-auto-session-manager`
**Audience:** Codex / AI agents working in this repo.
**Read first:** `CONTEXT.md` (orientation + known doc conflicts). This file is rules, not background — go there for the "why."

---

## 1. Source-of-Truth Order

When docs disagree, resolve in this order. If the conflict is a **safety-critical numeric value** (thresholds, timeouts, %), do not pick one — stop and ask. See `CONTEXT.md` §7 for the currently known conflicts.

1. `docs/RISK_AND_RUNBOOK.md` — for anything runtime-safety related (kill switch, circuit breakers, failover, reconciliation behavior)
2. `docs/DEFINITION_OF_DONE.md` — for what "complete" means on any PR
3. `docs/DATA_MODEL.md` — for schemas, field names, types. **Never guess a field name or shape — if it's not in this doc, stop and ask rather than inventing it.**
4. `docs/ARCHITECTURE.md` — for structure, component boundaries, tech stack
5. `docs/MVP_SCOPE.md` — for what's actually in scope right now (overrides `docs/PRD.md` when they conflict on scope)
6. `docs/PRD.md` — vision/rationale only; treat as aspirational for anything not also in `docs/MVP_SCOPE.md`

---

## 2. Non-Negotiable Rules

Straight from `docs/DEFINITION_OF_DONE.md` §4 ("Definition of NOT Done"). Violating any of these is an automatic reject, not a style nit.

| Rule | Detail |
| :--- | :--- |
| No `float` for money | Prices, sizes, PnL are always `decimal.Decimal`. `price = 64000.50` is wrong; `Decimal("64000.50")` is right. |
| No hardcoded secrets | API keys, Telegram tokens, DB passwords come from `.env` via Pydantic `Settings` — never inline, never in a default value. |
| No silent failures | `except: pass` is banned. Every caught exception either re-raises, degrades explicitly, or logs to Postgres/Telegram. |
| No blocking the event loop | `time.sleep()` and blocking `requests` calls are banned inside `asyncio` code. Use `await asyncio.sleep()` / async HTTP clients. |
| Every position gets an exchange-side SL | The Bybit Executor must place a hard Stop-Loss on the exchange server immediately on fill — not "eventually," not "if convenient." This is the single most safety-critical line of code in the repo. |
| No guessing field names | Use the strict Pydantic models in `docs/DATA_MODEL.md`. No raw `data['price']`-style dict access outside `app/data/normalizer.py`. |
| AI mandatory in safe positions | Pre-entry CryptoAnalyst and post-entry PositionJudge are not optional. LLM calls only via 9router proxy — never in the execution path (SOR/risk gate). See `docs/review/ai_layer_analysis.md`. |

---

## 3. Directory Map

```text
app/
├── main.py              # asyncio loop entrypoint — all 7 keys start here
├── core/
│   ├── config.py         # Pydantic Settings, loads .env — secrets live ONLY here
│   ├── database.py        # Postgres async pool (asyncpg)
│   ├── redis_client.py    # Redis async client (aioredis)
│   ├── session.py         # UTC session/regime logic (not one of the "6 Keys" — see CONTEXT.md #5)
│   ├── state.py            # In-memory state + Postgres sync (Key 5)
│   ├── trade_store.py      # Postgres trade CRUD
│   ├── ai_client.py        # 9router async HTTP client (AI layer)
│   ├── metrics.py          # Prometheus metrics (counters, gauges, histograms)
│   └── position_store.py   # Redis-backed position lifecycle state
├── data/                  # Key 1 — Global Data Engine
│   ├── ccxt_manager.py     # CCXT Pro WS + load_markets() symbol validation
│   ├── normalizer.py       # ONLY place raw exchange dicts get touched directly
│   ├── filters.py            # Bad tick rejection
│   ├── ohlcv_fetcher.py      # Cached OHLCV REST fetcher
│   ├── universe_scorer.py    # Dynamic universe scoring (Volume+Momentum+Squeeze+Overextension)
│   └── sector_mapping.py     # Static sector classification with keyword fallback
├── alpha/                  # Key 2 — Alpha Bridge
│   ├── metrics.py
│   ├── signals.py            # Multi-signal composite (skew+lead_lag+funding+OI)
│   ├── regime.py             # Hurst + ADX regime classifier
│   ├── lead_lag_buffer.py    # 15-min rolling price buffer
│   ├── entry_filter.py       # Pre-entry structural checklist (5 checks)
│   ├── ta_tools.py           # Deterministic TA indicators (RSI, BB, MACD, ATR, EMA)
│   ├── analyst.py            # AI pre-entry analyst (MANDATORY, via 9router)
│   ├── position_judge.py     # AI position judge (MANDATORY, 2-tier escalation)
│   ├── multi_tf.py           # Multi-timeframe confirmation (4H trend filter)
│   └── trade_memory.py       # Trade history injection for AI context
├── execution/               # Key 4 — Bybit Executor
│   ├── bybit_client.py       # Bybit REST/WS client + exchange-side SL
│   ├── sor.py                # Post-Only -> Reprice -> Market
│   └── position_lifecycle.py # Trailing stop + performance checkpoints
├── risk/                     # Key 3 — 3-Layer Risk Gate
│   ├── gates.py
│   ├── circuit_breaker.py
│   └── sector_cap.py         # Sector diversity cap (max 2 per sector)
├── watchdog/                 # Key 6
│   ├── monitor.py              # Heartbeat monitor, latency tracker, event loop lag
│   └── dead_mans_switch.py     # External health ping
└── bot/                      # Key 7 — Telegram Command Interface
    ├── handlers.py           # All command & callback handlers
    ├── runner.py             # PTB app builder, bot_data wiring, startup
    ├── alert_service.py      # Telegram alert sender
    └── utils/
        ├── format.py
        ├── telegram_helpers.py
        └── formatters/
            └── trade_history_formatter.py
tests/                        # see TESTING_STRATEGY.md for full layout
```

---

## 4. Before Writing Any Code

1. Identify which of the 6 Keys (or `core/`) the change touches — read that component's section in `docs/ARCHITECTURE.md` and its checklist in `docs/DEFINITION_OF_DONE.md` §3.
2. If the change touches a Pydantic model or DB table, cross-check `docs/DATA_MODEL.md` field-for-field. Don't extrapolate a shape.
3. If the change touches execution, risk gates, or the watchdog, re-read the relevant section of `docs/RISK_AND_RUNBOOK.md` — these are the parts of the system where a plausible-looking shortcut can lose real money.
4. Check `CONTEXT.md` §7 for whether this area of the system has an open doc conflict. If yes, do not silently resolve it — ask.

---

## 5. Testing Requirements

Full detail in `docs/TESTING_STRATEGY.md`. Minimum bar for any PR:

- New logic in `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, or `app/data/filters.py` → unit tests with >90% coverage, including the mandatory edge cases (divide-by-zero, missing exchange, bad tick, empty book).
- New DB writes → integration test asserting the row matches `docs/DATA_MODEL.md` schema exactly.
- Anything touching kill switch, circuit breakers, reconciliation, or proxy failover → a corresponding test from `docs/TESTING_STRATEGY.md` §5 must exist and pass. If no such test exists yet for the behavior you're adding, write it — don't skip it.
- Run locally before claiming done: `pytest`, `ruff check .`, `black --check .`, `mypy --strict app/`.

---

## 6. Do NOT

- Do not add Grafana, a microservice split, or an LLM in the hot execution path (SOR/risk gate). **AI is mandatory in two safe positions** (pre-entry CryptoAnalyst, post-entry PositionJudge) via 9router proxy — do not make it optional or skippable. See `docs/review/ai_layer_analysis.md` for the latency math.
- Do not weaken or bypass the Kill Switch, Circuit Breakers, or Startup Reconciliation for convenience during development (e.g., "just comment this out for local testing"). If it needs a dev-mode bypass, that bypass must be explicit, logged, and never the default.
- Do not invent a Prometheus metric name, Postgres column, or Pydantic field that isn't in `docs/DATA_MODEL.md` or `docs/DEFINITION_OF_DONE.md` §4 without flagging it as a new addition for review.
- Do not mark something "done" without walking the actual `docs/DEFINITION_OF_DONE.md` checklist for that component — not just "tests pass."

---

## 7. Conflict Resolution Protocol

If you (Codex) find a requirement that contradicts another doc — including the ones already logged in `CONTEXT.md` §7 — do not guess which one wins based on which seems more recent or more detailed. State the conflict plainly, cite both sources, and ask. This has already happened in this doc set (drawdown threshold, Redis scope) — it will happen again as the docs evolve, and picking silently is how a numeric typo becomes a real drawdown incident.