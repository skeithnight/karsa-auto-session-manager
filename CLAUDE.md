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

Rationale + full detail: `AGENTS.md` §2 and §8.

---

## 3. Before Writing Code

1. Identify the component → read its section in `docs/ARCHITECTURE.md` + `docs/DEFINITION_OF_DONE.md`.
2. Touching a Pydantic model / DB table → cross-check `docs/DATA_MODEL.md` field-for-field.
3. Touching execution/risk/watchdog → re-read `docs/RISK_AND_RUNBOOK.md`.
4. Check `CONTEXT.md` §7 for an open conflict in this area before resolving anything yourself.
5. Touching `RegimeClassifier` / `StrategyRouter` / `ActivePositionManager` / `PortfolioRiskManager` → also read the matching Phase 6 spec doc (§1 above).

---

## 4. Dev

```bash
pytest && ruff check . && black --check . && mypy --strict app/
```

\>90% unit coverage for `app/alpha/`, `app/risk/`, `app/data/normalizer.py`, `app/data/filters.py`, incl. edge cases (empty candles, single-candle, all-flat, divide-by-zero, bad tick). Full bar: `AGENTS.md` §5 / `docs/TESTING_STRATEGY.md`.

---

## 5. Do NOT

No LLM in the hot path. No weakening kill switch / circuit breakers / reconciliation. No inventing a metric, column, or field not in `docs/DATA_MODEL.md` or `docs/METRICS_DICTIONARY.md`. No bypassing `PortfolioRiskManager`. No soft-coding the Regime Shift Kill Switch. No marking "done" without walking `docs/DEFINITION_OF_DONE.md`.

---

## graphify

Knowledge graph at `graphify-out/`. For codebase questions run `graphify query "<question>"` first when `graphify-out/graph.json` exists — `graphify path "<A>" "<B>"` for relationships, `graphify explain "<concept>"` for focused concepts. `graphify-out/wiki/index.md` for broad navigation over raw source browsing. `GRAPH_REPORT.md` only if query/path/explain don't surface enough. Run `graphify update .` after modifying code.
