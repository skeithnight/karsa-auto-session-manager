# Quant Persona Audit

**Date:** 2026-07-28  
**Audit Type:** Code audit of the quant-trader-persona refactor, plus validation runbook for containers.  
**Scope:** `app/`, container wiring, and supporting docs. This audit did **not** execute the 1-hour validation run; the runbook below is the procedure to do that safely.

---

## Verdict

The quant-trader-persona refactor is a **good architectural direction**, but it is **not fully production-active yet**.

What looks strong:

- edge-family decomposition
- modular score composition
- extracted sizing pipeline
- explicit scaffold labeling for research modules
- immutable execution-intent prototype

What is still not ready:

- the live and shadow runtime still use the old decision engine
- several extracted loops do not match current interfaces
- family-aware ranking depends on trade fields that are not currently persisted
- the ranking-gate string mismatch still exists in the v2 path

So the right interpretation is:

**This refactor is promising, but still in transition. It should be validated as “parallel infrastructure” first, not assumed live-active.**

---

## Audit Findings

## 1. High: the live and shadow containers still run the old decision engine

**Evidence**

- `app/consumer/live_loop.py` imports `DecisionEngine` from `app.consumer.decision_engine`
- `app/consumer/live_loop.py:1377` instantiates `DecisionEngine`
- `app/consumer/shadow_loop.py` imports `DecisionEngine` from `app.consumer.decision_engine`
- `app/consumer/shadow_loop.py:431` instantiates `DecisionEngine`
- `app/consumer/decision_engine_v2.py` exists, but `rg` shows no active runtime wiring to it

**Impact**

The new quant-persona refactor is not the runtime engine for `karsa-live` or `karsa-shadow`.  
That means:

- `ScoreComposer` is not driving real entries
- `SizingPipeline` is not the live sizing path
- `winning_family` attribution is not yet part of live trading behavior

**What to do**

Before calling the refactor “complete,” add an explicit rollout mode:

1. shadow-only V2 mode
2. live dual-run compare mode
3. full V2 cutover flag

---

## 2. High: the extracted ranking refresh loop is not compatible with current interfaces

**Evidence**

- `app/consumer/loops/ranking_refresh.py:40` calls `trade_store.get_recent_trades(count=100)`
- `app/core/trade_store.py:317` defines `get_recent_trades(limit: int = 50)`
- `app/consumer/loops/ranking_refresh.py:43` calls `ranking_engine.evaluate(trades)`
- `app/research/ranking_engine.py:25` expects `metrics`, not raw trades
- `app/consumer/loops/ranking_refresh.py:44-45` expects `decision.value`
- `app/research/ranking_engine.py:89-98` returns a dict with `"decision"` string, not an enum-like object

**Impact**

The extracted loop cannot be dropped into runtime safely. If wired as-is, it will fail or write the wrong shape.

**What to do**

Normalize the ranking contract:

1. raw trades → metrics conversion
2. `RankingEngine.evaluate()` output → plain machine-readable enum/string
3. Redis write format → same across old and new loops

---

## 3. High: the extracted ELO and gate-calibration loops also use incompatible trade-store contracts

**Evidence**

- `app/consumer/loops/elo_refresh.py:30` calls `trade_store.get_recent_trades(count=50)`
- `app/consumer/loops/gate_calibration.py:30` calls `trade_store.get_recent_trades(count=100)`
- `app/core/trade_store.py:317` only supports `limit=`

There is also a data-shape issue:

- `app/consumer/loops/gate_calibration.py:43-52` expects `expected_value` in recent trade records
- `app/core/trade_store.py:330-341` does not return `expected_value`

**Impact**

Even if these loops were wired, they would not be calculating from the data they think they have.

**What to do**

Either:

1. extend `TradeStore` to persist and return the necessary fields, or
2. change the loops to use the actual stored schema

---

## 4. High: family-aware ranking cannot work yet because family attribution is not persisted in trade history

**Evidence**

- `app/risk/family_ranker.py:94` filters trades by `edge_family`
- `app/risk/family_ranker.py:124` expects `holding_time_minutes`
- `app/core/trade_store.py:317-341` returns recent trades without `edge_family`, `holding_time_minutes`, or `pnl_pct`
- `app/consumer/decision_engine_v2.py:523` attaches `winning_family` to the in-memory signal, but the active live path is still old engine

**Impact**

`FamilyRanker` currently has no reliable production data source.  
It may look complete, but it cannot yet produce meaningful family-aware rankings from current persisted trades.

**What to do**

Persist at least:

1. `edge_family`
2. `expected_value`
3. `holding_time_minutes`
4. normalized realized return metric

Then teach `TradeStore.get_recent_trades()` to return them.

---

## 5. Medium: the ranking-gate bug still survives in the new V2 engine

**Evidence**

- `app/consumer/decision_engine_v2.py:313-326` reads `karsa:ranking:decision`
- `app/consumer/decision_engine_v2.py:165-171` only blocks exact `"REJECT"`
- `app/research/ranking_engine.py:89-94` still produces decorated strings such as `"✅ PROMOTE"` and `"❌ REJECT"`

**Impact**

Even after switching to V2, the ranking gate can still silently behave incorrectly unless the Redis value format is normalized.

**What to do**

Use plain values only:

- `PROMOTE`
- `NEEDS_MORE_EVIDENCE`
- `REJECT`

Store human-friendly labels separately.

---

## 6. Medium: the refactor summary currently overstates production readiness

**Evidence**

- `docs/plan/REFACTORING_SUMMARY.md` describes the refactor as core-complete and broadly aligned
- runtime imports still show old engine usage
- extracted loops are not interface-clean yet
- `ExecutionIntent` is still described as a prototype in `app/core/execution_intent.py`

**Impact**

The documentation is ahead of the active runtime. That creates operator risk and false confidence during rollout.

**What to do**

Reframe status as:

1. implemented
2. unit-tested
3. runtime-wired
4. shadow-validated
5. live-active

The quant refactor is currently strongest in categories 1-2, not yet 4-5.

---

## 7. Medium: ExecutionIntent is a strong prototype, but not yet part of the execution contract

**Evidence**

- `app/core/execution_intent.py` is clearly labeled prototype/immutable contract
- `docs/V35_DECISION_INTELLIGENCE_GRAPH.md:697-698` still lists ExecutionIntent integration as pending
- no active live/shadow import path wires it into SOR or APM

**Impact**

The execution-intent model is a useful direction, but not yet a basis for claiming execution immutability in production.

**What to do**

Keep it prototype-scoped until:

1. signal → intent conversion exists in active runtime
2. SOR consumes intent constraints
3. APM lifecycle updates produce new intent versions

---

## 8. Low: the scaffold disclaimer is helpful and should stay

**Evidence**

- `app/research/SCAFFOLD_DISCLAIMER.md` clearly labels scaffold modules
- `app/research/experiment_runner.py` is still scaffold-grade by design

**Impact**

This is one of the cleaner parts of the refactor because it reduces false certainty.

**What to do**

Keep this pattern.  
It is a good precedent for any module that is not yet live-decision-grade.

---

## Audit Summary

### Good changes

- edge-family split is directionally right
- score composition is much easier to reason about than the monolith
- sizing extraction is a useful step toward parity
- scaffold labeling improves honesty

### Main blockers

1. runtime still uses the old decision engine
2. new helper loops do not match current interfaces
3. family-aware attribution is not yet persisted
4. ranking-gate normalization is still incomplete

### Recommendation

Treat the quant-persona refactor as **phase-ready for shadow validation**, not yet **production-complete**.

---

## Validation Runbook

This section is the practical validation procedure for:

- `karsa-data-engine`
- `karsa-live`
- `karsa-shadow`
- `karsa-backtest`

It starts with a rebuild and includes a **1-hour validation window**.

---

## Goal Of The 1-Hour Run

In one hour, we want to answer:

1. do all four containers boot cleanly?
2. is the data path healthy?
3. is live and shadow still stable after the quant refactor?
4. are any of the new quant-persona modules actually active in runtime?
5. does backtest worker stay healthy and ready?

---

## Step 0: Safety Notes

1. `karsa-live` is real-money connected per repo docs, not testnet.
2. Do **not** assume the quant refactor is active just because the files exist.
3. Do **not** run `down -v`.
4. Keep position size micro and operator attention high during validation.

---

## Step 1: Infra Check

If infra is not already running:

```bash
docker compose -f docker-compose.infra.yml up -d
```

Confirm infra:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep 'karsa-'
```

Expected healthy core services:

- `karsa-postgres`
- `karsa-redis`
- `karsa-gluetun`
- `karsa-9router`
- `karsa-prometheus`
- `karsa-grafana`

---

## Step 2: Rebuild Apps First

Required first step:

```bash
make rebuild
```

This rebuilds app containers only and should not touch infra.

After rebuild, confirm app containers are up:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep 'karsa-'
```

Expected app services:

- `karsa-data-engine`
- `karsa-live`
- `karsa-shadow`
- `karsa-backtest`
- `karsa-commander`

---

## Step 3: Immediate Boot Audit

Check the last 5 minutes of logs for each app container:

```bash
docker logs karsa-data-engine --since 5m
docker logs karsa-live --since 5m
docker logs karsa-shadow --since 5m
docker logs karsa-backtest --since 5m
docker logs karsa-commander --since 5m
```

Reject the build immediately if you see:

- traceback loops
- import errors
- repeated schema errors
- Redis/DB auth failures
- worker restart loops

---

## Step 4: Verify Role Wiring

The container roles are dispatched by `entrypoint.sh`. Confirm expected startup behavior:

- `karsa-data-engine` should run `app.data_engine.main`
- `karsa-live` should run `app.consumer.live_loop`
- `karsa-shadow` should run `app.consumer.shadow_loop`
- `karsa-backtest` should run `app.backtest.worker`
- `karsa-commander` should run `app.commander.main`

Quick check:

```bash
docker logs karsa-live --since 2m | grep 'ENTRYPOINT: KARSA_ROLE'
docker logs karsa-shadow --since 2m | grep 'ENTRYPOINT: KARSA_ROLE'
docker logs karsa-backtest --since 2m | grep 'ENTRYPOINT: KARSA_ROLE'
```

---

## Step 5: Quant Refactor Activation Check

This is the critical audit step.

### What to look for

If the quant-persona refactor is actually active in runtime, you should eventually see evidence of:

- `DecisionEngineV2`
- `ScoreComposer`
- `winning_family`
- family-aware scoring logs

### Practical log checks

```bash
docker logs karsa-live --since 10m | grep 'ScoreComposer'
docker logs karsa-shadow --since 10m | grep 'ScoreComposer'
docker logs karsa-live --since 10m | grep 'winning_family'
docker logs karsa-shadow --since 10m | grep 'winning_family'
```

### Interpretation

- If these are empty while normal signal evaluation logs continue, the old engine is still the active runtime path.
- That is currently expected from the code audit, and should be recorded as:

`PASS: containers healthy`

but

`FAIL: quant V2 path not yet runtime-wired`

---

## Step 6: Data Engine Validation

Validate `karsa-data-engine` for 1 hour.

### Log health

```bash
docker logs karsa-data-engine --since 10m | tail -n 200
```

Check for:

- steady ingestion
- no reconnect storms
- no repeated symbol-resolution failures

### Redis heartbeat

```bash
docker exec karsa-redis redis-cli GET system:heartbeat
```

### Global state sample

```bash
docker exec karsa-redis redis-cli KEYS 'global:state:*' | head
```

Expected:

- heartbeat value updates
- multiple `global:state:*` keys exist

---

## Step 7: Live Container Validation

Validate `karsa-live` for 1 hour.

### Basic log health

```bash
docker logs karsa-live --since 10m | tail -n 300
```

Check for:

- no crash loops
- no constant DB/Redis failures
- no repeated AI client failures
- no position-manager panic loops

### Signal path presence

```bash
docker logs karsa-live --since 10m | grep 'evaluate:'
```

Expected:

- signal evaluation is happening

### Ranking-gate sanity

```bash
docker exec karsa-redis redis-cli GET karsa:ranking:decision
```

Record the exact stored string.  
If it includes emoji, note that the normalization bug is still present.

---

## Step 8: Shadow Container Validation

Validate `karsa-shadow` for 1 hour.

### Basic log health

```bash
docker logs karsa-shadow --since 10m | tail -n 300
```

Check for:

- no crash loops
- no Redis namespace collisions
- no ShadowAPM exceptions looping

### Shadow mode isolation

```bash
docker exec karsa-redis redis-cli KEYS 'shadow:position:*' | head
```

Expected:

- only shadow positions use `shadow:position:*`

### Compare live vs shadow signal activity

```bash
docker logs karsa-live --since 1h | grep 'evaluate:' | wc -l
docker logs karsa-shadow --since 1h | grep 'evaluate:' | wc -l
```

This is not a profitability test. It is just a stability and path-activity sanity check.

---

## Step 9: Backtest Container Validation

Validate `karsa-backtest` for 1 hour.

### Worker health

```bash
docker logs karsa-backtest --since 10m
```

Expected:

- startup line similar to `BacktestWorker: starting BLPOP loop on backtest_jobs`
- no loop crashes

### Optional active queue test

Submit one manual job into Redis:

```bash
docker exec karsa-redis redis-cli LPUSH backtest_jobs '{"job_id":"manual-btc-001","symbol":"BTC/USDT","candle_limit":500}'
```

Then check:

```bash
docker logs karsa-backtest --since 5m
```

Expected:

- job received
- candles loaded
- results saved or a clear “insufficient candles” message

This validates the worker path end-to-end.

---

## Step 10: One-Hour Soak

Run the system for **1 hour** after rebuild.

Recommended monitoring loop every 10-15 minutes:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep 'karsa-'
docker logs karsa-data-engine --since 15m | tail -n 100
docker logs karsa-live --since 15m | tail -n 100
docker logs karsa-shadow --since 15m | tail -n 100
docker logs karsa-backtest --since 15m | tail -n 100
```

Record:

1. restarts
2. tracebacks
3. Redis/DB errors
4. AI failures
5. signal path activity
6. whether any V2-family logs appear

---

## Step 11: Pass/Fail Criteria

### Minimum pass

1. all containers stay up for 1 hour
2. no repeated fatal traceback loops
3. data-engine keeps heartbeats and global state fresh
4. live and shadow keep evaluating signals
5. backtest worker remains healthy

### Quant-refactor pass

To say the quant-persona refactor itself is validated in runtime, you also need:

1. proof that V2 modules are actually wired into live or shadow path
2. proof that family attribution appears in logs or persisted data
3. proof that extracted loops can run with current interfaces

### Current expected outcome from this code audit

Most likely result:

- **Container stability may pass**
- **Quant-refactor runtime activation will likely fail or remain unverified**

That would confirm the audit conclusion:

**good refactor direction, not yet fully runtime-integrated**

---

## Recommended Next Action After Validation

If the 1-hour run confirms the current audit, do this next:

1. wire `DecisionEngineV2` behind a runtime flag in shadow first
2. fix extracted loop interface mismatches
3. persist `edge_family` and `expected_value`
4. normalize ranking decision strings
5. rerun the same 1-hour validation

---

## Final Assessment

The quant-trader-persona implementation should be treated as:

- **architecturally meaningful**
- **partially implemented**
- **not yet fully activated in runtime**

That is a good place to be, as long as validation is strict and documentation stays honest.
