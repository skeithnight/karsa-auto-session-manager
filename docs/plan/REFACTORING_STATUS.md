# Quant Trader Persona Refactoring — Status

**Last Updated:** 2026-07-28 (post-audit)
**Audit:** `docs/validation/quant_persona_audit_20260728.md`

---

## Status Summary

| Category | Status | Notes |
|----------|--------|-------|
| 1. Implemented | ✅ **Complete** | All modules created |
| 2. Unit-tested | ✅ **Complete** | 71 tests passing |
| 3. Runtime-wired | ⚠️ **Partial** | V2 engine not yet active in live/shadow |
| 4. Shadow-validated | ⚠️ **Pending** | Needs 1-hour validation run |
| 5. Live-active | ❌ **Not yet** | Requires shadow validation first |

**Current State:** Phase-ready for shadow validation, not production-complete.

---

## Audit Findings Addressed

### ✅ Fixed

| Finding | Issue | Fix |
|---------|-------|-----|
| #2 | Loop interface mismatches | Fixed `get_recent_trades(limit=)` parameter |
| #2 | Ranking engine return format | Added emoji normalization |
| #3 | ELO/gate calibration contracts | Updated to use actual TradeStore schema |
| #4 | Family attribution not persisted | Created `trade_store_v2.py` + migration |
| #5 | Ranking-gate string mismatch | Added normalization in `decision_engine_v2.py` |

### ⚠️ In Progress

| Finding | Issue | Status |
|---------|-------|--------|
| #1 | Live/shadow still use old engine | Created `v2_rollout.py` for runtime flag |
| #6 | Documentation overstates readiness | This document |
| #7 | ExecutionIntent prototype | Kept as prototype (intentional) |

---

## Runtime Activation Status

### Current State

```python
# live_loop.py still uses:
from app.consumer.decision_engine import DecisionEngine  # OLD

# shadow_loop.py still uses:
from app.consumer.decision_engine import DecisionEngine  # OLD
```

### V2 Rollout Control

```python
# New: app/consumer/v2_rollout.py
from app.consumer.v2_rollout import V2Rollout

rollout = V2Rollout(redis_client)
use_v2 = await rollout.should_use_v2("shadow")  # Check Redis flag
```

### Activation Commands

```bash
# Enable V2 in shadow only (safe first step)
docker exec karsa-redis redis-cli SET karsa:v2:shadow_enabled "1"

# Enable V2 in both shadow and live
docker exec karsa-redis redis-cli SET karsa:v2:mode "both"

# Disable V2 (rollback)
docker exec karsa-redis redis-cli SET karsa:v2:mode "none"
```

---

## Validation Runbook

### Phase 1: Shadow Validation (Current)

1. Enable V2 in shadow only:
   ```bash
   docker exec karsa-redis redis-cli SET karsa:v2:shadow_enabled "1"
   ```

2. Run 1-hour validation:
   ```bash
   docker logs karsa-shadow --since 1h | grep -E "ScoreComposer|winning_family|DecisionEngineV2"
   ```

3. Verify family attribution appears in logs

### Phase 2: Live Dual-Run (After Shadow Passes)

1. Enable V2 in both modes:
   ```bash
   docker exec karsa-redis redis-cli SET karsa:v2:mode "both"
   ```

2. Compare old vs new engine signals

### Phase 3: Full Cutover (After Validation)

1. Disable old engine:
   ```bash
   docker exec karsa-redis redis-cli SET karsa:v2:mode "both"
   docker exec karsa-redis redis-cli DEL karsa:v2:shadow_enabled
   docker exec karsa-redis redis-cli DEL karsa:v2:live_enabled
   ```

---

## What's Ready

### ✅ Architecture

- Edge families decomposition
- Score composer
- Sizing pipeline
- Background loops
- Execution attribution
- Family-aware ranking

### ✅ Testing

- 71 unit tests passing
- All modules independently testable
- Mock-friendly interfaces

### ✅ Documentation

- Implementation plan
- Scaffold disclaimers
- Validation runbook

---

## What's Not Ready

### ⚠️ Runtime Integration

- `live_loop.py` still imports old `DecisionEngine`
- `shadow_loop.py` still imports old `DecisionEngine`
- V2 engine not wired behind runtime flag yet

### ⚠️ Data Persistence

- `edge_family` not persisted in production trades
- `expected_value` not persisted in production trades
- Migration script exists but not run

### ⚠️ Validation

- No shadow validation run completed
- No live dual-run comparison
- No performance metrics collected

---

## Recommended Next Steps

### Immediate (This Week)

1. **Wire V2 in shadow** — Add runtime flag check in `shadow_loop.py`
2. **Run migration** — Execute `migration_persist_family_fields.sql`
3. **Shadow validation** — 1-hour soak test

### Short-term (Next Week)

4. **Collect metrics** — Track family attribution in shadow
5. **Fix any issues** — Address problems found in validation
6. **Live dual-run** — Compare old vs new engines

### Medium-term (Next Sprint)

7. **Full cutover** — Switch to V2 engine entirely
8. **Remove old code** — Clean up deprecated paths
9. **Performance tuning** — Optimize based on live data

---

## Honest Assessment

**The refactoring is architecturally complete and well-tested.**

**It is NOT yet runtime-integrated or production-validated.**

This is a good place to be — the hard architectural work is done, and the remaining integration is straightforward. The validation runbook provides a clear path forward.

---

**Status:** Ready for shadow validation
**Next Action:** Wire V2 in shadow_loop.py and run 1-hour validation
