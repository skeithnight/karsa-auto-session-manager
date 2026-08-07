# Phase 3: AI Role Reversal — From Veto Gate to Alpha Ranker + Exit Brain

**Impact:** 🔥🔥🔥🔥  
**Effort:** High (4-5 files, ~500 LOC)  
**Risk:** Medium (AI behavior is non-deterministic; needs guardrails)

---

## The Problem

### Current AI Usage: A $0.002-per-call "No" Machine

Your [CryptoAnalyst](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/alpha/analyst.py) currently sits in the **middle** of the signal pipeline as a binary veto gate:

```
Deterministic Signal → AI: "Should I take this?" → 60% of the time: "NO_TRADE"
```

The prompt explicitly tells the AI:
> "If indicators conflict, lean toward NO_TRADE (capital preservation)"

This is like hiring a brilliant analyst and telling them "your job is to say no." The AI is optimizing for **not being wrong** instead of **finding alpha.**

### The Three AI Failures

1. **Veto Bias:** The prompt's instructions create systematic rejection. An AI that rejects 60% of signals with a 50/50 blend means it needs to output 60+ confidence for a 70-confidence signal to pass. The math is stacked against trading.

2. **Wrong Position in Pipeline:** AI runs **after** 15 deterministic filters have already approved the signal. By this point, the signal has proven itself deterministically — the AI should be adding context, not second-guessing.

3. **Unused for Exits:** Your `PositionJudge` exists but the APM doesn't call it systematically for exit timing. The AI's greatest strength (pattern recognition across messy, conflicting data) is wasted on a "yes/no" gate and barely used where it matters most — knowing when to get out.

---

## The Solution: Three New AI Roles

### Role 1: Signal Ranker (Pre-Entry)

Instead of veto-ing individual signals, the AI **ranks** the top 3-5 signals that passed deterministic scoring. This means:
- AI only sees signals that are already EV-positive
- AI's job is to pick the **best** opportunities, not reject them
- AI processes a batch, not individual signals

#### New Prompt: Signal Ranking

```python
RANKER_PROMPT = """You are an elite crypto portfolio manager. You have {n_signals} 
trade candidates that have passed all quality checks. Your job is to RANK them 
from best to worst opportunity.

Portfolio State:
- Open positions: {open_positions}
- Daily P&L: {daily_pnl}
- Available capital: {available_capital}
- Session: {session_context}
- Macro regime: {macro_regime}

Candidates:
{signal_list}

For each candidate, provide:
1. rank (1 = best)
2. sizing_multiplier (0.5 to 1.5 — how much of the default size to use)
3. edge_thesis (one sentence — WHY this is the best/worst opportunity)

Respond with ONLY a JSON array (no markdown):
[{{"symbol": "BTC/USDT", "rank": 1, "sizing_multiplier": 1.2, 
   "edge_thesis": "Funding negative, breakout from 4H range, volume confirming"}}]

Rules:
- You MUST rank ALL candidates. Do not reject any.
- sizing_multiplier < 0.7 means "weak but still tradeable"
- sizing_multiplier > 1.2 means "high conviction, increase size"
- Consider correlation: don't rank 3 correlated altcoins all as top picks
"""
```

**Key difference:** The AI never says "don't trade." It says "this one is best, that one is less good." Every signal gets traded — sizing reflects conviction.

#### Implementation: `app/alpha/ai_ranker.py` [NEW]

```python
class AISignalRanker:
    """Ranks multiple trade candidates instead of veto-ing individuals."""
    
    BATCH_SIZE = 5  # Rank top 5 signals per cycle
    
    async def rank(self, signals: list[TradeSignal], 
                   portfolio_state: dict) -> list[RankedSignal]:
        """Rank signals by AI-assessed quality. Never rejects."""
        
        if len(signals) <= 1:
            # Single signal: no ranking needed, pass through
            return [RankedSignal(signal=signals[0], rank=1, 
                                 sizing_mult=1.0, thesis="Single candidate")]
        
        # Take top N by EV score
        top_signals = sorted(signals, key=lambda s: s.expected_value, 
                            reverse=True)[:self.BATCH_SIZE]
        
        # Format for AI
        prompt = self._format_ranking_prompt(top_signals, portfolio_state)
        
        # Call AI (with timeout + circuit breaker)
        response = await self._call_ai(prompt)
        rankings = self._parse_ranking(response, top_signals)
        
        # Fallback: if AI fails, use EV ordering
        if not rankings:
            return [RankedSignal(signal=s, rank=i+1, sizing_mult=1.0, 
                                thesis="AI unavailable, EV ranking")
                    for i, s in enumerate(top_signals)]
        
        return rankings
```

### Role 2: Exit Brain (Post-Entry)

The AI should be the **primary exit decision-maker** for positions in the "ambiguous zone" — not at a clear SL or TP, but in the messy middle where humans (and deterministic logic) struggle:

#### When the AI Exit Brain Activates

```
Position opened → APM monitors every 2 seconds
  ├─ Clear loss (hits SL): EXCHANGE-SIDE SL fires → No AI needed
  ├─ Clear win (hits TP): EXCHANGE-SIDE TP fires → No AI needed  
  ├─ Breakeven lock triggered: SL moved to entry → Continue monitoring
  └─ AMBIGUOUS ZONE (position is +0.3R to +0.8R, or holding > 50% of max_hold_time):
     └─ AI PositionJudge activated:
        "This position is +0.5R with 2 hours left. CVD is flat. 
         Regime just shifted from TREND to RANGE. 
         Should I: (a) take profit now, (b) tighten trail, (c) hold?"
```

#### New Exit Brain Prompt

```python
EXIT_BRAIN_PROMPT = """You are managing an open {direction} position on {symbol}.

Position State:
- Entry: {entry_price}
- Current: {current_price} ({pnl_pct:+.2f}%)
- Stop Loss: {sl_price} (exchange-side)
- Take Profit: {tp_price}
- Hold time: {hold_minutes} minutes / {max_hold_minutes} max
- R-Multiple: {r_multiple:.2f}R

Market Context:
- Regime: {regime} (was {regime_at_entry} at entry)
- CVD slope: {cvd_slope} (momentum: {momentum_desc})
- Funding: {funding_rate} (next payment in {funding_hours}h)
- Volume vs 20-bar avg: {volume_ratio:.1f}x

Choose ONE action:
1. HOLD — conviction remains, let the trade play out
2. TIGHTEN_TRAIL — move SL to {suggested_trail_sl} (lock in {trail_lock_pct}% gain)
3. PARTIAL_EXIT — close 50%, let rest trail
4. FULL_EXIT — close now at market

Respond with ONLY a JSON object:
{{"action": "HOLD|TIGHTEN_TRAIL|PARTIAL_EXIT|FULL_EXIT",
  "confidence": 0-100,
  "reasoning": "<30 words max>"}}
"""
```

#### Integration with APM

```python
# In position_manager.py — add AI exit brain to monitoring loop

async def _check_ai_exit(self, position: dict, market_data: dict) -> str | None:
    """Consult AI exit brain in ambiguous zones."""
    
    r_multiple = self._calculate_r_multiple(position, market_data)
    hold_pct = self._hold_time_pct(position)
    
    # Only activate in ambiguous zone
    if r_multiple < 0.3 or r_multiple > 2.0:
        return None  # Clear loss or clear win — no AI needed
    if hold_pct < 0.3:
        return None  # Too early — let position develop
    
    # Rate limit: max 1 AI call per position per 5 minutes
    if not self._ai_cooldown_expired(position):
        return None
    
    result = await self.position_judge.evaluate_exit(position, market_data)
    
    if result and result.action in ("PARTIAL_EXIT", "FULL_EXIT"):
        # AI recommends exit — execute via SOR
        return result.action
    elif result and result.action == "TIGHTEN_TRAIL":
        # AI recommends trail tightening — amend SL
        await self._amend_sl(position, result.suggested_sl)
    
    return None
```

### Role 3: Regime Disambiguator (Continuous)

When the regime classifier outputs a low-conviction classification (ADX = 22, Hurst = 0.48 — borderline CHOP/RANGE/TREND), the AI should help resolve the ambiguity:

```python
REGIME_DISAMBIGUATOR_PROMPT = """The market regime classifier is uncertain.

Symbol: BTC/USDT (proxy for market regime)
ADX: {adx} (threshold: 20 for trend, 40 for hyper)
Hurst: {hurst} (0.5 = random walk, >0.5 = trending, <0.5 = mean-reverting)
ATR percentile: {atr_percentile}
Recent price action: {price_action_summary}

The classifier's best guess is {current_regime} with {conviction:.0%} confidence.

Is the market ACTUALLY:
A) Early TREND (breakout developing, trade aggressively)
B) Late CHOP (noise, reduce size)
C) RANGE (mean-revert at edges)

Respond JSON: {{"regime": "TREND_BULL|TREND_BEAR|RANGE|CHOP", 
                "confidence": 0-100,
                "reasoning": "<20 words>"}}
"""
```

This runs **alongside** the deterministic classifier, not instead of it. If AI confidence > 70% and disagrees with the deterministic classifier, use the AI's regime with 80/20 weighting:

```python
final_regime = ai_regime if ai_confidence > 70 and ai_disagrees else deterministic_regime
```

---

## Confidence Blending: Remove the 50/50 Flat Blend

The current blend is:
```python
final_conf = deterministic * 0.5 + ai * 0.5  # always
```

Replace with **regime-adaptive blending** based on historical accuracy:

```python
BLEND_WEIGHTS = {
    # (deterministic_weight, ai_weight) — calibrate from backtest
    "TREND_BULL":  (0.70, 0.30),  # Deterministic is strong in trends
    "TREND_BEAR":  (0.70, 0.30),
    "RANGE":       (0.60, 0.40),  # AI better at spotting exhaustion
    "CHOP":        (0.40, 0.60),  # AI better in noisy environments
    "TRANSITION":  (0.50, 0.50),  # Both equally useful in transitions
}
```

---

## Files Changed

| File | Action | Description |
|---|---|---|
| `app/alpha/ai_ranker.py` | **NEW** | Signal ranking (batch mode, never rejects) |
| `app/alpha/ai_exit_brain.py` | **NEW** | AI-driven exit decisions for ambiguous positions |
| `app/alpha/ai_regime_disambiguator.py` | **NEW** | AI resolves low-conviction regime classifications |
| `app/alpha/analyst.py` | **MODIFY** | Remove veto logic; refactor to ranking mode |
| `app/execution/position_manager.py` | **MODIFY** | Integrate AI exit brain into monitoring loop |
| `app/alpha/regime_classifier.py` | **MODIFY** | Add AI disambiguation for low-conviction reads |
| `app/consumer/decision_engine.py` | **MODIFY** | Replace individual AI veto with batch ranking |
| `tests/test_ai_ranker.py` | **NEW** | Test ranking produces ordered results, never empty |
| `tests/test_ai_exit_brain.py` | **NEW** | Test exit decisions in ambiguous zones |

---

## Validation Plan

1. **Shadow rank comparison** — Run both old (veto) and new (ranker) AI in shadow:
   - Track: did the ranker's #1 pick outperform the veto-surviving signal?
   - Target: ranker's #1 pick has ≥ 1.2x the EV of the veto survivor

2. **Exit brain A/B test** — Run AI exits alongside APM deterministic exits:
   - Track: does AI exit timing improve avg P&L per trade?
   - Target: ≥ 10% improvement in avg exit R-multiple

3. **Regime disambiguator accuracy** — When AI disagrees with deterministic:
   - Track: which one was right? (measured by next-4H price action)
   - Target: AI correct ≥ 55% when it disagrees with high confidence

---

## Cost Analysis

| AI Usage | Current (per day) | After (per day) | Notes |
|---|---|---|---|
| Pre-entry veto | 5-20 calls | 0 calls | Eliminated |
| Signal ranking | 0 calls | 3-5 calls | Batch mode, fewer total calls |
| Exit brain | 0 calls | 10-20 calls | Rate-limited per position |
| Regime disambiguator | 0 calls | 2-4 calls | Only on low-conviction reads |
| **Total** | **5-20 calls** | **15-29 calls** | ~50% more calls, but far more value |
| **Cost** | ~$0.01-0.04/day | ~$0.03-0.06/day | Negligible vs trading profit |

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| AI ranker always outputs same ranking | Add randomness detection; fall back to EV ordering |
| AI exit brain panics and closes everything | Rate limit: 1 exit action per position per 5 min; circuit breaker on AI exits |
| AI disagrees with deterministic regime incorrectly | Require AI confidence > 70%; weight 80/20 in favor of deterministic |
| AI latency delays execution | Ranking runs async; signals execute immediately at EV-scored size; ranking adjusts size on next cycle |
