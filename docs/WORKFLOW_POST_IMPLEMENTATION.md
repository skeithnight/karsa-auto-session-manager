# Post-Implementation Workflow

## Wire → Integrate → Calibrate → Observe → Validate → Learn & Adapt

After each feature is implemented, run this 6-stage workflow before promoting to live.

---

## Stage 1: WIRE

**Goal:** Connect new component to the pipeline. Verify it receives data and produces output.

### Checklist

- [ ] Import in target module (e.g., `decision_engine.py`, `live_loop.py`)
- [ ] Instantiate in `__init__` or `main()`
- [ ] Call in correct pipeline position (check call order)
- [ ] Read from correct Redis keys / config
- [ ] Write to correct Redis keys / metrics
- [ ] Background loop started (if async refresh needed)
- [ ] Cleanup in shutdown (task.cancel())
- [ ] No import errors on container start

### Verification Commands

```bash
# Check container starts without errors
docker logs karsa-live --since 2m | grep -i "import\|traceback\|fatal"

# Check Redis keys are being written
docker exec karsa-redis redis-cli keys "karsa:NEW_KEY:*"

# Check Prometheus metrics appear
curl -s localhost:9090/metrics | grep "new_metric_name"
```

### Gate Criteria
- Container starts clean (no import errors)
- New Redis keys appear within 1 cycle
- No crashes in first 5 minutes

---

## Stage 2: INTEGRATE

**Goal:** Verify the component works with all neighbors. Test data flow end-to-end.

### Checklist

- [ ] Upstream sends correct data format
- [ ] Component processes data correctly
- [ ] Downstream receives expected output
- [ ] Error handling: component degrades gracefully on upstream failure
- [ ] Error handling: component degrades gracefully on Redis failure
- [ ] Error handling: component degrades gracefully on timeout
- [ ] Metrics increment correctly (counters, histograms)
- [ ] No race conditions with concurrent coroutines
- [ ] Shadow mode parity (live + shadow produce same results)

### Verification Commands

```bash
# Check data flow in live logs
docker logs karsa-live --since 10m | grep "NEW_COMPONENT"

# Check shadow parity
docker logs karsa-shadow --since 10m | grep "NEW_COMPONENT"

# Check Redis state
docker exec karsa-redis redis-cli get "karsa:ranking:decision"

# Stress test: trigger multiple evaluations
docker logs karsa-live --since 5m | grep "evaluate:" | wc -l
```

### Gate Criteria
- Live and shadow produce identical outputs for same inputs
- All 3 error paths (Redis down, timeout, bad data) handled without crash
- Metrics increment on each evaluation

---

## Stage 3: CALIBRATE

**Goal:** Tune parameters for current market conditions. Find optimal operating point.

### Checklist

- [ ] Default parameters documented (what they do, valid range)
- [ ] Sensitivity analysis: what happens at min/max values
- [ ] Backtest with historical data at different parameter sets
- [ ] Shadow mode run at 3 parameter sets (conservative, default, aggressive)
- [ ] Compare Sharpe, win rate, max drawdown across parameter sets
- [ ] Select best parameter set based on risk-adjusted returns
- [ ] Document calibration date and market conditions
- [ ] Set auto-recalibration schedule (daily/weekly)

### Calibration Table Template

| Parameter | Min | Default | Max | Best | Date | Market |
|-----------|-----|---------|-----|------|------|--------|
| gate_threshold | 50 | 75 | 95 | 72 | 2026-07-28 | RANGE |
| elo_k_factor | 16 | 32 | 64 | 32 | 2026-07-28 | Mixed |
| regime_confidence_blend | 0.3 | 0.5 | 0.7 | 0.6 | 2026-07-28 | High vol |

### Gate Criteria
- Backtest Sharpe > 1.0 at selected parameters
- Shadow mode shows positive EV over 24h
- No parameter causes > 20% drawdown in backtest

---

## Stage 4: OBSERVE

**Goal:** Monitor real-world behavior. Collect statistics. Find anomalies.

### Checklist

- [ ] Run in shadow mode for minimum 48 hours
- [ ] Collect at least 100 evaluation cycles
- [ ] Collect at least 20 trade signals (even if rejected)
- [ ] Check for unexpected rejection patterns
- [ ] Check for score distribution anomalies
- [ ] Check for latency spikes
- [ ] Check for error rate spikes
- [ ] Compare live vs shadow decision divergence
- [ ] Check Redis memory usage (no leaks)
- [ ] Check container memory/CPU usage (no leaks)

### Metrics to Track

| Metric | Target | Alert If |
|--------|--------|----------|
| Evaluation latency (p99) | < 500ms | > 2000ms |
| Error rate | < 1% | > 5% |
| Redis key count growth | Stable | > 2x baseline |
| Container memory | Stable | > 80% limit |
| Shadow divergence rate | < 5% | > 15% |
| Signal generation rate | > 10/hour | < 1/hour |

### Gate Criteria
- 48h shadow run with < 5% divergence from live
- No error rate spikes
- Latency p99 < 1000ms
- No Redis memory growth

---

## Stage 5: VALIDATE

**Goal:** Compare observed behavior against expectations. Pass/fail each hypothesis.

### Checklist

- [ ] Define success criteria BEFORE observing (pre-register hypothesis)
- [ ] Compare actual metrics vs expected metrics
- [ ] Statistical significance test (if applicable)
- [ ] Check for regime-dependent performance
- [ ] Check for symbol-dependent performance
- [ ] Check for time-of-day effects
- [ ] Document any unexpected behaviors
- [ ] Decision: PROMOTE / TUNE / REJECT

### Validation Report Template

```markdown
## Feature: [Name]
**Hypothesis:** [What we expected]
**Observation:** [What actually happened]
**Metric:** [Quantified result]
**Verdict:** PASS / FAIL / PARTIAL
**Decision:** PROMOTE / TUNE / REJECT
**Notes:** [Any anomalies or learnings]
```

### Gate Criteria
- PASS: All success criteria met, statistical significance achieved
- PARTIAL: Some criteria met, tuning needed
- FAIL: Core hypothesis invalidated, feature rejected

---

## Stage 6: LEARN & ADAPT

**Goal:** Extract insights. Update models. Feed back into next iteration.

### Checklist

- [ ] Update feature importance rankings (from Alpha Attribution)
- [ ] Update ELO ratings (from trade outcomes)
- [ ] Update gate threshold (from calibration loop)
- [ ] Update regime probabilities (from HMM observations)
- [ ] Update similarity index (from new trade data)
- [ ] Update counterfactual analysis (from closed trades)
- [ ] Document learnings in `docs/LEARNINGS.md`
- [ ] Create next iteration tasks (back to Stage 1)
- [ ] Update CLAUDE.md if new hard rules discovered

### Feedback Loop

```
Learn & Adapt
    ↓
Insights → New hypotheses
    ↓
Hypotheses → New features (back to Wire)
    ↓
New features → Wire → Integrate → Calibrate → Observe → Validate → Learn & Adapt
    ↓
(Cycle repeats)
```

---

## Quick Reference: Stage Duration

| Stage | Minimum | Recommended | Maximum |
|-------|---------|-------------|---------|
| Wire | 1 hour | 1 day | 2 days |
| Integrate | 2 hours | 2 days | 3 days |
| Calibrate | 4 hours | 3 days | 5 days |
| Observe | 24 hours | 48 hours | 7 days |
| Validate | 2 hours | 1 day | 2 days |
| Learn & Adapt | 1 hour | 1 day | 2 days |
| **Total** | **~32 hours** | **~9 days** | **~16 days** |

---

## Automation Opportunities

### Stage 1: Wire — Manual (code changes)
### Stage 2: Integrate — Partially automatable (integration tests)
### Stage 3: Calibrate — Automatable (backtest + shadow comparison)
### Stage 4: Observe — Automatable (monitoring dashboards + alerts)
### Stage 5: Validate — Partially automatable (metric comparison scripts)
### Stage 6: Learn & Adapt — Automatable (background loops already exist)

### Suggested Automation Stack

```
Wire → Git commit + Docker rebuild
Integrate → pytest integration suite
Calibrate → Walk-forward optimizer + shadow comparison
Observe → Prometheus + Grafana dashboards + alerting
Validate → Validation script (compare metrics vs thresholds)
Learn & Adapt → Background loops (gate calibration, ELO, feature importance)
```
