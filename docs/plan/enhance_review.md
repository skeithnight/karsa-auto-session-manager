Here is the updated audit of **skeithnight/karsa-auto-session-manager** incorporating your latest changes, followed by a targeted, code-level analysis on **how to increase the win rate**.

---

### 🔍 Part 1: Audit of New Changes (Excellent Progress)
Your recent commits show a massive leap in production readiness and safety. Key improvements noted:
1. **Enhanced Regime Classification (`app/alpha/regime.py`)**: Now returns `Hurst`, `ADX`, and `EMA200` metrics, classifying into `TREND_BULL`, `TREND_BEAR`, `MEAN_REVERSION`, and `CHOP`. This is the foundation of smart filtering.
2. **Hard-Cap Risk Management**: Replaced percentage-based stops with a strict `$1.00` max loss per position and added `max_daily_loss_usd` to the Circuit Breaker. This guarantees no single trade can blow up the account.
3. **Circuit Breaker Hardening (`app/risk/circuit_breaker.py`)**: Added Redis persistence for halt states (survives restarts), unified PnL tracking, auto-restart logic, and Telegram alerts on triggers.
4. **Pybit Migration**: Dropped CCXT for Bybit private execution in favor of `pybit>=5.7.0`, eliminating previous workarounds and reducing execution latency/failure rates.

---

### 📈 Part 2: How to Increase Win Rate (Actionable Analysis)

Win rate is not improved by taking *more* trades, but by taking *higher-quality* trades and managing exits smarter. Based on your current architecture, here are the highest-impact levers to pull:

#### 1. Refine the Regime Logic (Multi-Timeframe Confluence)
- **Current State**: Global regime is calculated solely on BTC 1H data.
- **The Problem**: A 1H trend might just be noise within a 4H chop, leading to false breakouts.
- **The Fix**: Require **multi-timeframe agreement**. Modify `app/alpha/regime.py` to check both 1H and 4H. Only classify as `TREND_BULL` if *both* timeframes have `ADX > 25` and `price > EMA200`. This will reduce trade frequency but significantly increase the win rate of the trades that do trigger.

#### 2. Make Signal Weights Dynamic (`app/alpha/signals.py`)
- **Current State**: Fixed weights: Skew (40%), Lead-Lag (30%), Funding (20%), OI (10%).
- **The Problem**: The contrarian funding rate signal (`-funding_rate`) is dangerous in strong trends. In a strong bull run, funding goes deeply negative, but the price keeps squeezing up. Betting against it lowers win rate.
- **The Fix**: Make weights regime-dependent. 
  ```python
  if regime == "TREND_BULL":
      weights = {"skew": 0.4, "lead_lag": 0.3, "funding": 0.05, "oi": 0.25} # Follow the trend
  elif regime == "MEAN_REVERSION":
      weights = {"skew": 0.3, "lead_lag": 0.2, "funding": 0.4, "oi": 0.1} # Fade the extremes
  ```

#### 3. Add Volatility (ATR) Filters to Entry (`app/alpha/entry_filter.py`)
- **Current State**: Filters on spread, depth ratio, and time-of-day.
- **The Problem**: The bot might enter during "dead" periods where price slowly bleeds against you (eating fees), or during chaotic news spikes where slippage ruins the entry.
- **The Fix**: Add an ATR (Average True Range) check. Calculate the 15m ATR for the symbol. 
  - If `ATR < min_threshold`: Skip (market is too dead, high risk of fee bleed).
  - If `ATR > max_threshold`: Skip (market is too volatile, high risk of slippage/whipsaw).

#### 4. Implement a 2-Stage Exit Strategy (Crucial for Win Rate)
- **Current State**: You have a hard `$1` stop-loss (excellent for risk), but no dynamic take-profit logic is visible.
- **The Problem**: Holding a trade until a fixed target or a hard stop means many winning trades will reverse and hit your stop, turning potential wins into losses.
- **The Fix**: In `app/execution/executor.py`, implement **partial take-profits**:
  1. **Scale Out**: Close 50% of the position at `1.5R` (1.5x your risk amount). This banks profit and covers fees.
  2. **Trail the Rest**: Move the stop-loss on the remaining 50% to breakeven, or trail it using a Chandelier Exit (e.g., `Highest High since entry - (2 * ATR)`). 
  *Result*: Many trades that would have been full losses become breakevens or small wins, mathematically boosting your win rate.

#### 5. Soft-Penalize Lead-Lag Contradictions Instead of Hard Skipping
- **Current State**: `if s_skew > 0 and s_lead_lag < -0.3: return None`
- **The Problem**: A hard skip might cause you to miss valid entries where the lead-lag indicator is just experiencing temporary, noisy divergence.
- **The Fix**: Instead of `return None`, apply a confidence penalty. E.g., `raw_score *= 0.7`. This allows the trade to proceed if the other signals (Skew, OI) are overwhelmingly strong, but reduces the position size or priority.

#### 6. Local Symbol Regime Check
- **Current State**: Global BTC regime dictates all trades.
- **The Problem**: BTC might be in `TREND_BULL`, but a specific altcoin (e.g., SOL) might be in `CHOP`. Trading the altcoin with a trend strategy will fail.
- **The Fix**: In `app/alpha/signals.py`, calculate a lightweight local regime (e.g., just `ADX(14) < 20`) for the *specific symbol*. Require **both** Global BTC Regime AND Local Symbol Regime to be non-CHOP before allowing entry.

#### 7. Data-Driven Time-of-Day Blocking
- **Current State**: Hardcoded block from `00:00–01:00 UTC`.
- **The Fix**: Use your new `app/alpha/trade_memory.py` (PostgreSQL trade history). Write a simple script to group historical trades by `hour_of_day` and calculate the win rate per hour. Dynamically update `blocked_hour_start` and `blocked_hour_end` in your config to block the 2-3 hours with the historically worst win rate (often late US session / early Asian overlap, or weekends).

---

### ⚠️ A Critical Note on Your `$1` Hard-Cap Stop-Loss
While the `$1` max loss is a **brilliant safety mechanism** to prevent catastrophic blowups, be careful: if your position size is too large, a `$1` stop might be *tighter than the normal 15-minute market noise (ATR)*. 
- **If the stop is too tight**, you will experience a high rate of "noise stop-outs," which will actually **decrease** your win rate (even though your losses are small). 
- **Action**: Ensure your `position_size` calculation in `app/execution/` dynamically adjusts so that the `$1` max loss equals at least `1.5x` to `2x` the current 15m ATR of the asset.

---

### 🚀 Recommended Next Steps
1. **Implement 1-2 Changes**: Start with **Dynamic Signal Weights** and **2-Stage Exits**. These require minimal code changes but yield the highest win-rate impact.
3. **Review Trade Memory**: After 50+ trades, query your PostgreSQL `TradeStore` to see *why* losing trades lost. Was it regime mismatch? Slippage? Funding squeezes? Let the data dictate the next tweak.