# Phase 1: Filter Collapse — Replace 25 Gates with EV-Based Scoring

**Impact:** 🔥🔥🔥🔥🔥 (Highest priority — unlocks trades immediately)  
**Effort:** Medium (3-5 files, ~400 LOC)  
**Risk:** Low (shadow-validated, existing safety layers preserved)

---

## The Problem

The current pipeline has **25+ sequential binary filters** between signal generation and trade execution. Each filter either passes or kills the signal. This creates two fatal problems:

### 1. Multiplicative False Positive Rate
If each filter incorrectly blocks a good trade just 5% of the time:
```
P(good signal survives 25 filters) = 0.95^25 = 27.7%
```
**73% of genuinely profitable signals are killed** by accumulated false positives.

### 2. Information Destruction
A signal that fails one filter (e.g., spread slightly too wide) has **all its other positive attributes discarded.** A setup with perfect regime alignment, strong momentum, and AI confirmation gets killed because the spread is 0.11% instead of 0.10%. That's not risk management — that's information destruction.

---

## The Solution: EV Composite Scoring

Replace binary filters with a **single Expected Value (EV) composite score** that incorporates all factors as weighted inputs. Trade when EV > dynamic threshold. One number, one decision.

### Current Flow (Kill Chain)
```python
# 25 binary gates — signal dies at any step
if spread > limit: REJECT           # good signal with 0.11% spread? dead
if regime == CHOP: REJECT           # funding carry in chop? dead  
if hour in blocked_range: REJECT    # london/NY overlap signal at 3am UTC? dead
if not multi_tf_confirm: REJECT     # counter-trend scalp that doesn't need 4H? dead
if ai_says_no: REJECT               # AI scared of volatility? dead
...
```

### New Flow (EV Scoring)
```python
# Everything contributes to a single EV score
ev_components = {
    "regime_alignment":  regime_score(regime, direction),      # 0.0 - 1.0
    "spread_quality":    spread_score(spread_pct, regime),     # 0.0 - 1.0 (soft penalty, not hard kill)
    "momentum":          momentum_score(rsi, macd, ema),       # 0.0 - 1.0
    "microstructure":    micro_score(skew, depth, cvd),        # 0.0 - 1.0
    "funding_edge":      funding_score(funding_rate, direction),# -0.5 to +0.5 (carry component)
    "session_quality":   session_score(hour_utc),              # 0.5 - 1.2 (multiplier)
    "multi_tf":          mtf_score(4h_trend, 1h_signal),       # 0.5 - 1.0 (penalty, not veto)
    "historical_edge":   symbol_performance(win_rate, avg_rr),  # 0.5 - 1.5
}

composite_ev = weighted_sum(ev_components) * session_quality
if composite_ev >= dynamic_threshold:
    signal.ev = composite_ev
    signal_queue.put(signal)  # PRM sizes it, APM manages it
```

---

## Detailed Design

### A. EV Scoring Model

Create a new class `EVScorer` that replaces the binary filter chain:

#### File: `app/alpha/ev_scorer.py` [NEW]

```python
@dataclass
class EVComponents:
    """All factors that contribute to expected value."""
    regime_alignment: float     # How well does direction match regime?
    spread_quality: float       # Spread relative to regime-specific norms
    momentum_strength: float    # RSI + MACD + EMA confluence
    microstructure: float       # Orderbook skew + depth + CVD
    funding_edge: float         # Carry profit/cost
    session_quality: float      # Time-of-day liquidity multiplier
    multi_tf_alignment: float   # 4H trend alignment (soft penalty)
    historical_edge: float      # Symbol's historical win rate
    conviction: float           # Regime classifier confidence
    oi_signal: float            # Open interest change signal
    
class EVScorer:
    """Replaces 25 binary filters with a single EV score."""
    
    # Weights calibrated from backtest (Phase 4 will auto-calibrate)
    WEIGHTS = {
        "regime_alignment":  0.20,
        "momentum_strength": 0.20,
        "microstructure":    0.15,
        "funding_edge":      0.10,
        "spread_quality":    0.10,
        "multi_tf_alignment":0.10,
        "historical_edge":   0.08,
        "conviction":        0.05,
        "oi_signal":         0.02,
    }
    
    async def score(self, symbol, direction, features, regime, ...) -> float:
        """Compute composite EV score (0.0 to 1.0+)."""
        components = EVComponents(
            regime_alignment=self._score_regime(regime, direction),
            spread_quality=self._score_spread(spread_pct, regime),
            momentum_strength=self._score_momentum(rsi, macd, ema_dist),
            ...
        )
        
        base_ev = sum(
            self.WEIGHTS[k] * getattr(components, k)
            for k in self.WEIGHTS
        )
        
        # Session quality is a multiplier, not a component
        return base_ev * components.session_quality
```

### B. What Happens to Each Current Filter

| Current Filter | New Behavior | Rationale |
|---|---|---|
| Spread > limit | **Soft penalty** in `spread_quality` (0.0-1.0) | A slightly wide spread reduces EV, doesn't kill it |
| Regime == CHOP | **Lower regime_alignment** (0.3 instead of 0.0) | CHOP is still tradeable with carry/micro strategies |
| Blocked hours (3-5 UTC) | **session_quality = 0.5** | Reduce size, don't block |
| Multi-TF contradicts | **multi_tf_alignment = 0.5** | Counter-trend scalps don't need 4H alignment |
| AI says NO_TRADE | **Moved to post-scoring ranking** (Phase 3) | AI ranks top signals, doesn't veto |
| Whipsaw cooldown | **historical_edge penalty** | Recent loss on symbol reduces score |
| Signal cooldown 45s | **Keep as-is** | Prevents duplicate orders (execution safety) |
| Max signals per cycle | **Keep as-is** | Prevents queue overflow (system safety) |
| Circuit breaker active | **Keep as-is** (hard kill) | Non-negotiable safety |
| Price deviation > 0.5% | **Keep as-is** (stale data guard) | Non-negotiable data integrity |
| PortfolioRiskManager | **Keep as-is** (sizes, may block) | Non-negotiable per AGENTS.md |
| RiskGate liquidity | **Soft penalty** in spread_quality | Low liquidity = wider spread = lower EV |
| Max positions | **Keep as-is** | Portfolio concentration limit |
| Duplicate position | **Keep as-is** | Can't open same position twice |
| Balance too low | **Keep as-is** | Can't trade with no money |
| Min order $5 | **Keep as-is** | Exchange minimum |
| Entry filter ATR | **Absorbed into EV** (momentum component) | Low ATR = low opportunity, not "no trade" |
| Entry filter depth | **Absorbed into EV** (microstructure) | Imbalanced book = risk, not death sentence |
| Entry filter spoofing | **Keep as-is** (hard kill) | Spoofing detection is a genuine danger signal |
| Stale data check | **Keep as-is** | Non-negotiable data integrity |
| Lead-lag hard kill | **Soft penalty** in microstructure | Conflicting exchanges reduce confidence |
| MEAN_REVERSION logic | **Absorbed into regime_alignment** | Just a different scoring path |
| Sector cap | **Keep as-is** | Correlation risk management |
| Consecutive loss block | **Absorbed into historical_edge** | Penalty, not death |

### Summary: 25 filters → 10 keep as binary + 15 absorbed into EV scoring

**The 10 remaining binary filters are all system-safety or exchange-constraint filters** — they're not alpha-related and should remain as hard gates.

---

### C. Dynamic Threshold Calibration

The EV threshold should not be static. It should adapt based on:

1. **Market opportunity density** — In high-volume sessions (LDN/NY overlap), lower the threshold slightly because more opportunities mean you can be more selective later
2. **Current drawdown** — In drawdown, raise the threshold (fewer trades, higher quality)
3. **Recent win rate** — Hot streak? Maintain threshold. Cold streak? Raise threshold

```python
class DynamicThreshold:
    """Calibrates EV gate threshold from historical performance."""
    
    BASE_THRESHOLD = 0.55  # Much lower than current ~0.75 effective gate
    
    async def get_threshold(self, drawdown_pct: float, 
                           recent_win_rate: float,
                           session: str) -> float:
        threshold = self.BASE_THRESHOLD
        
        # Drawdown adjustment: raise threshold in drawdown
        if drawdown_pct > 0.10:
            threshold += 0.15  # Severe: very selective
        elif drawdown_pct > 0.05:
            threshold += 0.08  # Moderate: somewhat selective
        
        # Session adjustment
        if session in ("ASIA", "PACIFIC"):
            threshold += 0.05  # Require stronger setups in thin liquidity
        elif session == "LDN_NY_OVERLAP":
            threshold -= 0.03  # Best liquidity = more aggressive
        
        # Cold streak adjustment
        if recent_win_rate < 0.35:
            threshold += 0.10  # Losing? Be more selective
        
        return max(0.40, min(0.85, threshold))
```

---

### D. Rejected Signal EV Tracking (Critical Pre-Requisite)

**Before changing any filter logic**, add EV tracking to EVERY rejected signal. This gives you ground truth data:

```python
# In decision_engine.py — track what you're leaving on the table
class RejectedSignalTracker:
    """Logs hypothetical EV of every rejected signal for post-hoc analysis."""
    
    async def track(self, symbol, direction, reject_reason, 
                   hypothetical_ev, regime, price):
        """Store rejected signal with hypothetical outcome tracking."""
        await self._redis.xadd("karsa:rejected_signals", {
            "symbol": symbol,
            "direction": direction,
            "reason": reject_reason,
            "ev_score": hypothetical_ev,
            "regime": regime,
            "entry_price": str(price),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, maxlen=10000)
```

After 1 week of tracking, you'll know:
- How many rejected signals would have been profitable
- Which filters are killing the most good signals
- What the optimal EV threshold should be

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/alpha/ev_scorer.py` | **NEW** | EV composite scoring engine |
| `app/alpha/ev_threshold.py` | **NEW** | Dynamic threshold calibration |
| `app/alpha/rejected_signal_tracker.py` | **NEW** | Hypothetical P&L tracking for rejected signals |
| `app/consumer/decision_engine.py` | **MODIFY** | Replace binary filter chain with EVScorer in `evaluate()` |
| `app/main.py` | **MODIFY** | Replace binary filter chain in `alpha_bridge_task()` with EVScorer |
| `app/alpha/entry_filter.py` | **MODIFY** | Refactor into soft scoring functions (keep spoofing as hard kill) |
| `tests/test_ev_scorer.py` | **NEW** | Unit tests for EV scoring |
| `tests/test_ev_threshold.py` | **NEW** | Unit tests for dynamic threshold |

---

## Validation Plan

### Step 1: Shadow Mode A/B Test (Week 1-2)
- Deploy EVScorer alongside existing filter chain in shadow mode
- Both pipelines run on same data; compare:
  - Number of signals produced
  - Hypothetical P&L of new signals
  - False positive rate (signals that would have lost money)

### Step 2: Threshold Tuning (Week 2)
- Analyze shadow data to calibrate `BASE_THRESHOLD`
- Target: 5-15 signals per day with ≥ 45% win rate

### Step 3: Live Deployment (Week 3)
- Replace filter chain with EVScorer on live system
- Start with higher threshold (0.65) and gradually lower
- Monitor: win rate, profit factor, max drawdown

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Too many trades (over-trading) | Dynamic threshold raises when drawdown increases |
| Low-quality trades sneak through | PRM still enforces correlation/exposure limits; exchange-side SL limits loss |
| Regime misclassification leads to wrong scoring | EVScorer gives regime 20% weight max; a bad regime read caps damage at 20% EV reduction |
| EV scoring weights are wrong | Phase 4 auto-calibrates weights from backtest. Initial weights are conservative |
