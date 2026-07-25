# 🧠 Brainstorm: Catching Crypto's Big Gainers

> **Core observation**: In crypto, 5% of tokens generate 80% of total returns. The current system treats every signal equally — it doesn't have a mechanism to **identify, enter early, and ride** the tokens that are about to do +30%, +50%, +100%. This brainstorm explores ideas to fix that.

---

## The Problem in One Chart

```
Current System Behavior:
                                                    ← System exits here (trailing stop)
Token price: ───────────────╱╲──╱──────────────────╱╱╱╱╱╱╱╱╱──────
                           ↑                      ↑
                    System enters here        The REAL move happens
                    (+3% move captured)       (+40% total, system missed +37%)
```

The system is designed for **scalping the first impulse**. It enters when momentum appears, takes a small profit on trailing stop, and moves on. But crypto's profit distribution is **fat-tailed** — the big money comes from riding runners, not scalping noise.

---

## 🎯 THEME 1: Early Mover Detection — Get In Before the Crowd

### Idea 1: "New Listing Sniper"
**Insight**: Tokens listed on Bybit in the last 7-14 days have a disproportionately high probability of a +30% move. The exchange listing itself is a catalyst, and the first major pullback after listing often becomes the highest R:R entry of the token's life.

**How it works**:
- Monitor Bybit's new listings API (or compare today's `load_markets()` vs yesterday's cached version)
- When a new token is listed, add it to a **watchlist** with a 14-day "new listing bonus" flag
- During these 14 days, lower the strategy gate threshold by 20% for this token (it needs less confluence because the listing IS the catalyst)
- Combine with a "first pullback" detector: wait for the initial pump to fade, then enter on the first 20-30% retrace from the post-listing high

**Why it increases profit**: New listings are structurally undertraded by bots (no historical data = most systems skip them). Humans FOMO in late. Being early and systematic beats both.

---

### Idea 2: "Volume Anomaly Detector" (Pre-Pump Scanner)
**Insight**: Before a token pumps +30%, there's almost always a **volume precursor** — unusual volume (2-5x average) appearing 2-6 hours BEFORE the price move. This is insiders/whales accumulating.

**How it works**:
- Every 15 minutes, scan all universe symbols for **volume anomaly**: `current_4h_volume > 3x rolling_7d_average_4h_volume` AND `price_change_4h < 5%`
- The key filter: volume is spiking but price hasn't moved yet. This means someone is accumulating at the bid (absorbing sell pressure)
- Flag these tokens for **priority evaluation** — bump them to front of the decision pipeline queue
- If the accumulation pattern persists for 2+ consecutive 15min checks, auto-boost the token's strategy score by +15

**Why it increases profit**: You're front-running the public move by detecting whale footprints. By the time the price starts moving (and the universe scanner picks it up via momentum), you're already in.

---

### Idea 3: "Funding Rate Extreme Reversal"
**Insight**: When funding rate goes extremely negative (< -0.05%), it means shorts are paying 0.05% every 8 hours to stay short. This is unsustainable. These positions WILL cover, and when they do, the price rockets.

**How it works**:
- Track funding rate history per symbol (last 3 funding periods = 24h)
- When funding hits extreme negative AND price has stopped falling (flattened or slight uptick), it's a **short squeeze setup**
- Enter LONG with tight stop below the 24h low, targeting +10-20% as the shorts cover
- The current system already has `funding_score` in UniverseScorer but it doesn't distinguish direction and doesn't track the *persistence* of extreme funding

**Why it increases profit**: Short squeezes are some of the most violent and profitable moves in crypto. They're also highly predictable — extreme funding + price stabilization = high probability setup.

---

### Idea 4: "OI + Price Divergence Detector"
**Insight**: When Open Interest is rising rapidly but price is flat or falling, new short positions are being built aggressively. When these shorts are wrong, the liquidation cascade creates a massive pump.

**How it works**:
- Track `OI_change_24h` vs `price_change_24h` per symbol
- Flag **Bearish OI Buildup**: OI up >10%, price flat/down = shorts piling in
- Flag **Bullish OI Buildup**: OI up >10%, price flat/up = longs piling in
- When the OI buildup reaches extreme (>20% in 24h) and a directional trigger fires (breakout above 24h high for bearish buildup), this is a **liquidation cascade entry**
- Current system has binary OI (`1.0 or -1.0`) — this needs to be a continuous signal with magnitude

**Why it increases profit**: Liquidation cascades cause 10-50%+ moves in minutes. They're mechanical (forced buying/selling) and therefore highly reliable once triggered.

---

## 🎯 THEME 2: The "Runner Ride" System — Let Winners Run

### Idea 5: "Tiered Exit Strategy" (The 80/20 Rule)
**Insight**: The current system exits 100% of the position on trailing stop. This means every winner captures only the first leg. In crypto, the first leg is often 3-5%, but the full move is 20-50%.

**How it works**:
- Split every position into 3 tranches:
  - **Tranche 1 (40%)**: Take profit at +1.5R (quick win, locks profit)
  - **Tranche 2 (40%)**: Trailing stop at 2x ATR (rides the trend)
  - **Tranche 3 (20%)**: "Moon bag" — ultra-wide trailing stop at 5x ATR or regime-change exit only
- The moon bag stays open as long as the trend regime persists
- This is particularly powerful in TREND_BULL and HYPER_BULL regimes where the current system's tight trailing stop (3x ATR) gets triggered by normal retracements

**Why it increases profit**: Even if the moon bag gets stopped out at breakeven 50% of the time, the other 50% when it catches a +20% runner more than compensates. Expected value is strongly positive because crypto returns are fat-tailed.

---

### Idea 6: "Momentum Accumulation" (Add to Winners)
**Insight**: The current system enters once and manages the position defensively. Professional crypto traders **add to winning positions** as the trend confirms.

**How it works**:
- When an open position reaches +2R profit AND the regime is still TREND_BULL/HYPER_BULL:
  - Check if the latest candle just broke out above a new 20-period high
  - If yes, add 30% more to the position at the new price
  - Move the ENTIRE position's stop-loss to the original entry + fees (breakeven the new tranche immediately)
- Maximum 2 additions per position (so max 1.6x the original size)
- Only in trend regimes, never in RANGE or CHOP

**Why it increases profit**: In strong trends, the highest-probability entries are actually AFTER the initial move. Adding to winners compounds returns geometrically rather than linearly.

---

### Idea 7: "Regime-Aware Hold Duration"
**Insight**: The system uses fixed max hold times (2880 min for TREND, 240 for RANGE, 30 for CHOP). But some trends last days or weeks. Closing a +15% winner because "48 hours passed" is leaving money on the table.

**How it works**:
- Instead of fixed time exits, use **dynamic hold time based on position performance**:
  - If position is profitable (> +1R): extend hold time by 50% each checkpoint
  - If position is at breakeven (0 to +1R): keep original hold time
  - If position is losing (< 0): halve the remaining hold time
- In HYPER regimes, disable the time exit entirely — let the regime shift kill switch handle it
- This means a winning TREND position can theoretically hold for days if it keeps making new highs

**Why it increases profit**: Time-based exits are arbitrary and cause premature exit from the best trades. Performance-based duration naturally keeps winners open and cuts losers faster.

---

## 🎯 THEME 3: Narrative & Catalyst Trading — Ride the Story

### Idea 8: "Sector Momentum Cascade"
**Insight**: In crypto, narratives move in waves. When AI tokens pump, they ALL pump within 24-48 hours. When L2s pump, same thing. The current sector filter prevents concentration but doesn't **exploit** sector momentum.

**How it works**:
- Track sector-level momentum (average 4H return for all tokens in each sector)
- When a sector shows **acceleration** (today's 4H return > yesterday's 4H return for 3+ consecutive periods):
  - Mark it as a "hot narrative"
  - For the top 3 tokens in that sector, LOWER the strategy gate by 15%
  - INCREASE the max-per-sector cap from 2 to 3 positions for this narrative only
- When the sector acceleration decelerates (return flattens), revert to normal caps

**Why it increases profit**: The biggest crypto gains come from narrative waves. If AI tokens are pumping today, the highest EV play is to have maximum exposure to AI tokens, not to diversify into boring sectors.

---

### Idea 9: "BTC Dominance Rotation Signal"
**Insight**: When BTC dominance (BTC.D) is falling, it means money is rotating from BTC into altcoins. This is the "altseason" signal. During altseason, altcoins can do 50-200% while BTC does 5%.

**How it works**:
- Track BTC.D via `BTC/USDT market cap / total crypto market cap` (or approximate via BTC vs TOTAL exchange data)
- When BTC.D drops for 3+ consecutive days:
  - Shift universe scoring to favor smaller-cap altcoins (increase momentum weight, decrease volume weight)
  - Increase max position count for alts
  - Reduce confidence threshold for alt LONGs
- When BTC.D rises (money rotating back to BTC):
  - Shift to BTC/ETH only
  - Reduce alt exposure aggressively
  - Tighten alt stops

**Why it increases profit**: Altseasoning is one of the most reliable macro patterns in crypto. Systems that trade alts during altseason and BTC during BTC-season massively outperform systems that ignore this rotation.

---

### Idea 10: "Exchange Listing Arbitrage"
**Insight**: When a token gets listed on a new major exchange (Binance, Coinbase), it often pumps 20-50% on the exchange where it already trades (Bybit). These announcements are public and can be detected.

**How it works**:
- Periodically check Binance/Coinbase listing announcements (via RSS, Twitter API, or listing page scraping)
- When a token that's already on Bybit gets announced for Binance listing:
  - Immediately enter LONG (the market hasn't fully priced it in yet)
  - Set a 24-48h hold time (the pump usually peaks within 24h of announcement)
  - Use wide stops (1.5x normal ATR) because the volatility will be extreme but directionally favorable

**Why it increases profit**: Exchange listings are one of the few truly predictable catalysts in crypto. The supply/demand math is simple: new exchange = new buyers = price up.

---

## 🎯 THEME 4: Smart Re-Entry — Don't Miss the Second Wave

### Idea 11: "Winner Re-Entry Queue"
**Insight**: When the system exits a winning trade on trailing stop, the token often consolidates briefly and then resumes its trend. The current system treats this as a completely new signal evaluation, losing all context.

**How it works**:
- When a position closes profitably (> +2R), add the symbol to a **Re-Entry Queue** with:
  - The exit price, direction, and regime
  - A 4-hour watchlist timer
- During the 4-hour window, if the token pulls back 30-50% of the move and then shows a new momentum signal:
  - Fast-track it through the pipeline (skip multi-TF filter, lower gate by 10%)
  - Use the original entry thesis as context for the AI analyst
- This is essentially a "continuation trade" — different from a fresh entry

**Why it increases profit**: The most profitable continuation trades are on tokens you've already been right about. The first win proves the thesis. The re-entry captures the second (often larger) leg.

---

### Idea 12: "Dip Buyer on Strong Performers"
**Insight**: Tokens that have pumped +15%+ in the last 48h and then dip -5% to -10% often bounce hard. This is just profit-taking, not a reversal. The current system's overextension penalty actually PENALIZES these setups.

**How it works**:
- Track 48h performance for all universe symbols
- For tokens with +15% 48h performance:
  - DISABLE the overextension penalty (the penalty is designed for exhaustion, but strong performers are just consolidating)
  - Set a "dip buy zone" at -5% to -10% from the 48h high
  - When price enters this zone AND shows a bullish candle (hammer, engulfing), generate a LONG signal with 2x normal confidence
- Stop loss below the 48h low (clear invalidation level)

**Why it increases profit**: The current overextension penalty in `UniverseScorer` actively prevents the system from trading the strongest performers. In crypto, "overextended" tokens are often the ones that go even higher.

---

## 🎯 THEME 5: Portfolio-Level Alpha — Think Beyond Single Trades

### Idea 13: "Correlated Momentum Basket"
**Insight**: Instead of treating each position independently, identify 3-5 tokens that move together and enter them as a basket. If 1 token starts pumping, the others will follow within hours.

**How it works**:
- Compute rolling 24h Pearson correlation between all universe symbols
- Identify clusters of 3-5 tokens with correlation > 0.8
- When one token in the cluster triggers a signal and enters:
  - Pre-compute signals for the other cluster members
  - If they're within 15% of their own trigger threshold, fast-track them
- This is like buying the whole sector when one token leads

**Why it increases profit**: Crypto moves in packs. If you're LONG on FET/USDT and it's pumping, RENDER, AGIX, and OCEAN will follow. Missing the lagging legs is leaving money on the table.

---

### Idea 14: "Dynamic Risk Budget Based on P&L"
**Insight**: The system uses fixed risk per trade regardless of session performance. Professional traders increase size when they're "in the zone" (winning) and decrease when cold.

**How it works**:
- Track daily P&L in real-time
- Adjust the risk budget dynamically:
  - **Up +2% today**: Increase risk per trade by 30% (playing with house money)
  - **Flat (±0.5%)**: Normal risk
  - **Down -1%**: Reduce risk per trade by 30%
  - **Down -2%**: Reduce to minimum risk (0.5%)
  - **Down -3%**: Circuit breaker (existing)
- This creates a natural **anti-martingale** progression

**Why it increases profit**: When the system is correct about the market regime, it compounds gains faster. When it's wrong, it bleeds slower. Over time, this asymmetry dramatically improves the Sharpe ratio.

---

## 🎯 THEME 6: Anti-Bleed Defense — Stop the Slow Death

### Idea 15: "Regime Conviction Scaling"
**Insight**: Not all regime classifications have the same conviction. ADX=26 (barely trending) and ADX=45 (strongly trending) both get the same TREND treatment. The system should size proportionally to conviction.

**How it works**:
- Add a `regime_conviction` score (0.0 to 1.0) alongside the regime classification:
  - TREND with ADX=25: conviction = 0.3 (barely trending)
  - TREND with ADX=35: conviction = 0.6 (moderate trend)
  - TREND with ADX=50: conviction = 1.0 (strong trend)
  - RANGE with Hurst=0.3: conviction = 0.8 (strongly mean-reverting)
  - RANGE with Hurst=0.44: conviction = 0.3 (barely mean-reverting)
- Multiply position size by regime_conviction
- This ensures the system goes big only when the regime is clear

**Why it increases profit**: Most losses come from weak regime signals (ADX=22, ambiguous). By sizing proportionally to conviction, you naturally avoid the low-quality trades without adding another binary filter that kills valid signals.

---

### Idea 16: "Fee-Aware Minimum Profit Target"
**Insight**: Many trades exit at +0.1% to +0.3% profit. After Bybit fees (maker 0.02%, taker 0.055% × 2 legs = 0.11% round trip), funding costs, and slippage, these are actually break-even or slight losses.

**How it works**:
- Before placing any trade, calculate the **minimum profitable exit price**:
  ```
  min_profit = entry_fee + exit_fee + expected_funding_drag + slippage_estimate
  ```
- If the TP target is less than 2x this minimum (i.e., the R:R after costs is < 2:1), REJECT the trade
- This is particularly important for CHOP and RANGE regimes where the profit targets are small

**Why it increases profit**: Eliminating trades that can't meaningfully beat transaction costs removes the largest category of "small winners that are actually losers."

---

## 🏆 Top 5 Most Impactful Ideas (My Ranking)

| Rank | Idea | Why It's #1-5 |
|:-----|:-----|:-------------|
| **1** | **Tiered Exit (Moon Bag)** | Single biggest lever. Current system exits 100% too early. Keeping 20% as a moon bag would have captured the full move on every runner. |
| **2** | **Volume Anomaly Detector** | Gets you in BEFORE the move. The difference between +3% and +30% is often just 2-4 hours of earlier detection. |
| **3** | **Winner Re-Entry Queue** | The best signal is "I was just right about this token." Continuation trades have 2x higher win rate than fresh entries. |
| **4** | **Dip Buyer on Strong Performers** | Directly addresses the #1 missed opportunity: tokens that have already proven strength but get a temporary pullback. The overextension penalty is actively BLOCKING these. |
| **5** | **Regime Conviction Scaling** | Prevents the slow bleed from weak-conviction entries. Most losses come from "maybe trend?" trades at ADX 22-25. |

---

> [!TIP]
> These ideas are intentionally brainstorm-level. Want me to develop any of them into a full implementation plan with code changes, test cases, and risk analysis? Just say which ones interest you most.

> [!IMPORTANT]
> Several of these ideas (Tiered Exit, Momentum Accumulation, Dynamic Risk Budget) would require changes to the `ActivePositionManager` and `BybitExecutor` — which are safety-critical modules. They'd need careful design per `AGENTS.md` §2 rules before any code is written.
