# CLAUDE.md

`karsa-auto-session-manager` — crypto perps bot. Reads Binance/OKX/Bybit, executes Bybit-only via self-hosted WireGuard VPN (`gluetun` sidecar). Single-process asyncio, 15m–4h swing. AI via 9router (mandatory in safe positions only).

**Read first:** `CONTEXT.md` (orientation + open conflicts) → `AGENTS.md` (full rules, directory map, module personas) — only pull these into context when the task needs them, not by default.

---

## 1. Source-of-Truth Order

`docs/RISK_AND_RUNBOOK.md` > `docs/DEFINITION_OF_DONE.md` > `docs/DATA_MODEL.md` > `docs/ARCHITECTURE.md` > `docs/MVP_SCOPE.md` > `docs/PRD.md`.
Phase 6: `docs/architecture/adaptive_multi_strategy.md`, `docs/execution/active_position_manager.md`, `docs/risk/portfolio_risk_manager.md` are authoritative for their modules.
Safety-critical numeric conflict → **stop and ask**, never pick silently. Known open conflicts: `CONTEXT.md` §7.

---

## 2. Hard Rules (automatic reject if violated)

- Money is always `decimal.Decimal`, never `float`.
- Secrets only via `.env` → Pydantic `Settings`. Never inline.
- No `except: pass`. Log or re-raise.
- No blocking calls in `asyncio` code — `await asyncio.sleep()` / async HTTP only.
- Exchange-side SL on every fill, immediately, via Bybit API. Never in-memory-only tracking. TP the same.
- No guessing field names — `docs/DATA_MODEL.md` is truth.
- AI (CryptoAnalyst + PositionJudge, via 9router) is mandatory in safe positions, forbidden in the hot execution path (SOR/risk gate).
- Every entry passes `PortfolioRiskManager` before `BybitExecutor` — no bypass, ever, for any reason.
- Regime Shift Kill Switch is not a config toggle.
- Every `ActivePositionManager` async loop: `try/except` + `await asyncio.sleep()` on the error path. No bare infinite loops.
- Shadow mode (`SHADOW_MODE_ENABLED=true`) skips startup reconciliation and position_reconciler. Never run shadow against live Bybit positions without understanding this state.
- SL capped at 5% of entry price (APM `_reconcile_position` enforces `MAX_RISK_PCT = 0.05`).
- Orphan sync skips positions with notional < 5 USDT (below Bybit minimum order).

Rationale + full detail: `AGENTS.md` §2 and §8.

---

## 3. Before Writing Code

1. Identify the component → read its section in `docs/ARCHITECTURE.md` + `docs/DEFINITION_OF_DONE.md`.
2. Touching a Pydantic model / DB table → cross-check `docs/DATA_MODEL.md` field-for-field.
3. Touching execution/risk/watchdog → re-read `docs/RISK_AND_RUNBOOK.md`.
4. Check `CONTEXT.md` §7 for an open conflict in this area before resolving anything yourself.
5. Touching `RegimeClassifier` / `StrategyRouter` / `ActivePositionManager` / `PortfolioRiskManager` → also read the matching Phase 6 spec doc (§1 above).
6. Touching shadow system (`ShadowExecutor`, `ShadowAPM`, shadow stores) → read `docs/review/refinement_shadom_plan.md` for the 4 critical refinements (fee asymmetry, wick miss, funding drag, pending limits).
7. Touching `app/consumer/`, `app/commander/`, `app/backtest/`, `app/analytics/`, `app/data_engine/` → read the relevant section in `AGENTS.md` §3 directory map + agent section before writing.
8. Touching `app/research/` → read `app/research/ranking_engine.py` and `app/research/metrics_engine.py` first.
9. Touching `app/risk/volatility_surface.py` → understand it publishes to `karsa:vol_surface:*` Redis keys.

---

## 4. Dev

```bash
pytest && ruff check . && black --check . && mypy --strict app/
```

### Compose (split infra / apps)

Infra = `docker-compose.infra.yml` (postgres, redis, gluetun, 9router, prometheus, grafana).
Apps = `docker-compose.apps.yml` (data-engine, live, shadow, backtest, commander).

| Command | What it does |
|---------|-------------|
| `make up` | Cold start — both stacks together |
| `make rebuild` | Rebuild apps only, infra untouched |
| `make down` | Stop everything |
| `make logs` | Tail app logs |
| `make logs-infra` | Tail infra logs |
| `make cleanup` | Prune Docker disk usage (safe — unused only) |
| `make disk-check` | Alert if disk usage > 80% |
| `make db-maintenance` | Daily DB backup + cleanup (retention: 30d candles, 7d signals) |
| `make db-backup` | Backup DB only (no cleanup) |

**Never** run `docker compose up -d --build` without specifying apps file — it recreates infra containers (kills 9router state). Always use `make rebuild` or target apps file explicitly.

\>90% unit coverage for `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, `app/data/filters.py`, incl. edge cases (empty candles, single-candle, all-flat, divide-by-zero, bad tick). Full bar: `AGENTS.md` §5 / `docs/TESTING_STRATEGY.md`.

---

## 5. Do NOT

No LLM in the hot path. No weakening kill switch / circuit breakers / reconciliation. No inventing a metric, column, or field not in `docs/DATA_MODEL.md` or `docs/METRICS_DICTIONARY.md`. No bypassing `PortfolioRiskManager`. No soft-coding the Regime Shift Kill Switch. No marking "done" without walking `docs/DEFINITION_OF_DONE.md`.
No mixing shadow and live Redis keys. Shadow positions use `shadow:position:*` namespace exclusively.

---

## 6. Live Background Loops (live_loop.py)

| Loop | Interval | Redis Key | Purpose |
|------|----------|-----------|---------|
| `_ranking_refresh_loop` | 1h | `karsa:ranking:decision` | Strategy promotion gate |
| `_gate_calibration_loop` | 1h | `karsa:gate:dynamic_threshold` | Adaptive gate threshold from historical EV |
| `_elo_refresh_loop` | 5min | `karsa:elo:{strategy}` | Per-strategy ELO ratings |
| `_vol_surface_loop` | 30min | `karsa:vol_surface:*` | BTC/ETH volatility term structure |
| HMM classification | 1h | `system:hmm:regime` | Regime prediction with probabilities |
| GARCH forecast | 1h | `system:garch:volatility` | Volatility forecasting |

**Important**: `_ranking_refresh_loop` and `_elo_refresh_loop` use `trade_store.get_recent_trades()` (not `get_closed_trades()`). Column is `realized_pnl` (not `pnl_pct`), direction field is `side` (not `direction`).

---

## 7. Redis Key Schema

```
karsa:ranking:decision          — PROMOTE / NEEDS_MORE_EVIDENCE / REJECT
karsa:ranking:details           — JSON with metrics and reasons
karsa:gate:dynamic_threshold    — JSON: {threshold, median_ev, winning_trades, total_trades}
karsa:elo:{regime}:{direction}  — JSON: {elo, wins, losses, win_rate}
karsa:correlation:{symbol}      — JSON: {max_correlation, correlated_count} (5min TTL)
karsa:vol_surface:composite     — JSON: {surface: {btc, eth, spread}, timestamp}
karsa:vol_surface:btc           — JSON: {1h, 4h, 1d, composite}
karsa:vol_surface:eth           — JSON: {1h, 4h, 1d, composite}
karsa:vol_surface:spread        — JSON: {1h, 4h, 1d}
karsa:alert:rebalance           — JSON: rebalance opportunity alert (5min TTL)
system:hmm:regime               — JSON: {state, state_name, signal, probabilities, confidence}
system:regime:{symbol}          — Regime string (RANGE, TREND_BULL, etc.)
karsa:position:{symbol}:{side}  — Position state dict
```

---

## graphify

Knowledge graph at `graphify-out/`. For codebase questions run `graphify query "<question>"` first when `graphify-out/graph.json` exists — `graphify path "<A>" "<B>"` for relationships, `graphify explain "<concept>"` for focused concepts. `graphify-out/wiki/index.md` for broad navigation over raw source browsing. `GRAPH_REPORT.md` only if query/path/explain don't surface enough. Run `graphify update .` after modifying code.
