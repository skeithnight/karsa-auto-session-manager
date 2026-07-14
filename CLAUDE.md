# CLAUDE.md

`karsa-auto-session-manager` — crypto trading bot. Multi-exchange data (Binance, OKX, Bybit), Bybit-only execution via WARP proxy. Single-process asyncio monolith, 15m-4h swing.

**Read first:** `CONTEXT.md` (orientation + conflicts). `AGENTS.md` (directory map + full rules).

---

## 1. Source-of-Truth Order

When docs disagree: `RISK_AND_RUNBOOK.md` > `DEFINITION_OF_DONE.md` > `DATA_MODEL.md` > `ARCHITECTURE.md` > `MVP_SCOPE.md` > `PRD.md`. Safety-critical numeric conflicts → stop and ask. See `CONTEXT.md` §7.

---

## 2. Non-Negotiable Rules

| Rule | Detail |
|:---|:---|
| No `float` for money | Always `decimal.Decimal`. |
| No hardcoded secrets | `.env` via Pydantic `Settings` only. |
| No silent failures | `except: pass` banned. Log or re-raise. |
| No blocking event loop | `await asyncio.sleep()` / async HTTP only. |
| Exchange-side SL on every fill | Immediate on fill — most safety-critical code. |
| No guessing field names | Use `DATA_MODEL.md` Pydantic models. |
| AI mandatory in safe positions | CryptoAnalyst + PositionJudge via 9router. Not optional. Never in SOR/risk gate. |

---

## 3. Development

```bash
pytest && ruff check . && black --check . && mypy --strict app/
```

---

## 4. Before Writing Code

1. Identify component → read `ARCHITECTURE.md` + `DEFINITION_OF_DONE.md` checklist.
2. Pydantic model or DB table → cross-check `DATA_MODEL.md` field-for-field.
3. Execution/risk/watchdog → re-read `RISK_AND_RUNBOOK.md`.
4. Check `CONTEXT.md` §7 for open conflicts in this area.

---

## 5. Testing

Minimum: >90% unit coverage for alpha/risk/data. Integration tests for DB writes. Safety tests for kill switch/circuit breakers/reconciliation. Full strategy: `TESTING_STRATEGY.md`.

---

## 6. Do NOT

- No LLM in hot execution path (SOR/risk gate). AI mandatory in safe positions only.
- No weakening kill switch, circuit breakers, or reconciliation.
- No inventing metric names/columns/fields not in `DATA_MODEL.md` or `METRICS_DICTIONARY.md`.
- No marking "done" without walking `DEFINITION_OF_DONE.md` checklist.

---

## 7. New Docs (Institutional-Grade)

| Doc | Purpose |
|:---|:---|
| `SYSTEM_CONSTANTS.md` | Every numeric threshold with file:line |
| `EVENTS.md` | Producer/consumer contracts |
| `REDIS_OWNERSHIP.md` | Single-writer per key |
| `AI_INTERFACE.md` | AI request/response schemas |
| `CONFIGURATION.md` | Config hierarchy + reload semantics |
| `DATA_RETENTION.md` | Storage lifecycle |
| `OWNERSHIP.md` | Domain object ownership |
| `ADR/` | Architecture decision records (8 ADRs) |
| `METRICS_DICTIONARY.md` | Prometheus metric names |
| `TELEGRAM_INTERFACE.md` | Bot commands + alerts |

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
