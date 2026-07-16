Based on the architecture of **`karsa-auto-session-manager` (ASM)** and the specific "leaks" identified in your trading data (dust trades, taker fee drag, and choppy hour entries), here is a targeted, code-level analysis and improvement plan. 

We will map the fixes directly to ASM’s **"7 Keys" architecture** to ensure we don't break its core safety-first design.

---

### 🛑 PART 1: Fixing the Leaks (Immediate Actions)

#### Leak 1: The "Dust Trade" Problem (e.g., the $6.91 ATOM trade)
**The Cause**: The bot is calculating position sizes that fall below a logical profitability threshold, even if they meet Bybit's absolute minimum exchange limits.
**The Fix Location**: `app/risk/` (The 3-Layer Risk Gate)

**Action**: Inject a **Logical Minimum Notional Check** *before* the order reaches the executor.
```python
# In app/risk/risk_gate.py (or equivalent pre-trade check)
def validate_position_size(self, symbol: str, quantity: Decimal, price: Decimal) -> bool:
    notional_value = quantity * price
    
    # 1. Exchange hard limit (Bybit minimum)
    if notional_value < self.exchange_min_notional[symbol]:
        return False 
        
    # 2. LOGICAL PROFITABILITY LIMIT (The Fix)
    # If the position is under $50, the fee drag will eat the alpha.
    if notional_value < Decimal('50.0'): 
        self.logger.warning(f"Rejected {symbol}: Notional ${notional_value} below logical $50 threshold.")
        return False
        
    return True
```

#### Leak 2: The Taker Fee Bleed (Paying 0.055% instead of Maker)
**The Cause**: ASM uses Smart Order Routing (SOR): `Post-Only → Reprice → Market fallback`. The fallback to "Market" is triggering when the bot is impatient, causing it to pay Taker fees on both entry and exit.
**The Fix Location**: `app/execution/` (Bybit Executor)

**Action**: Tune the SOR patience and make exits asymmetric.
1. **Increase Reprice Patience**: If the bot falls back to Market too quickly, increase the `reprice_timeout` or `max_reprice_attempts` in your config. 
2. **Asymmetric Exit Routing**: Take Profits should *never* fall back to Market orders. 
```python
# In app/execution/bybit_executor.py
async def execute_exit(self, order_type: str, ...):
    if order_type == 'TAKE_PROFIT':
        # STRICT POST-ONLY FOR TPS. Do not fallback to market.
        # If it doesn't fill, let it sit on the book or cancel and reprice.
        return await self._place_post_only_limit(...)
        
    elif order_type == 'STOP_LOSS':
        # ONLY Stop Losses are allowed to use Market/Reduce-Only to guarantee execution.
        return await self._place_market_reduce_only(...)
```

#### Leak 3: Trading During "Chop" (00:32 UTC Asian Session)
**The Cause**: The bot is taking trend/mean-reversion signals during low-volume, sideways hours. 
**The Fix Location**: `app/core/session.py` (Session Orchestrator) & `app/alpha/` (Alpha Bridge)

**Action**: Implement a dual-layer "Chop Filter".
1. **Time-Block Restriction**: In `session.py`, define a "Low Volatility" UTC block where new entries are strictly forbidden.
```python
# In app/core/session.py
def is_entry_allowed(self, current_utc_hour: int) -> bool:
    # Block new entries during the dead Asian session (00:00 - 06:00 UTC)
    # unless a specific high-volatility event is detected.
    if 0 <= current_utc_hour < 6:
        return False 
    return True
```
2. **Alpha Bridge Volatility Filter**: Add an ADX or Hurst Exponent check. If the market is ranging, reject the signal.
```python
# In app/alpha/alpha_bridge.py
def calculate_signal_confidence(self, market_data):
    adx = market_data.indicators.adx
    # If ADX < 20, the market is choppy. Kill the signal.
    if adx < 20.0:
        return 0.0 
    # ... proceed with normal VWAP/Skew calculations
```

---

### 🚀 PART 2: Improving ASM for Higher Profitability

Once the leaks are fixed, you can leverage ASM’s unique architecture (specifically its **Global Data Engine**) to extract more alpha.

#### Improvement 1: Weaponize the "Lead-Lag" Global Data
ASM reads Binance and OKX but executes on Bybit. Currently, it uses this for "sentiment." Let's use it for **micro-structure execution**.
*   **The Idea**: In `app/alpha/`, calculate the real-time price delta between Binance and Bybit. 
*   **The Execution**: If Binance spot/perps spike +0.3% in 500ms, and Bybit hasn't moved yet, the bot should instantly cancel its passive Bybit Post-Only buy orders and reprice them *higher* (or even use a small Market buy) to front-run the inevitable Bybit catch-up. You are using global data as a literal price-prediction signal for your local execution venue.

#### Improvement 2: Funding Rate "Override" Logic
Perpetual funding rates are paid every 8 hours. ASM should use this to its advantage.
*   **The Idea**: In `app/core/session.py` or `app/risk/`, check the upcoming funding rate. 
*   **The Execution**: If the bot is in a profitable LONG position, and the funding rate is deeply negative (meaning shorts are paying longs a high fee), the bot should **delay its Take Profit exit** by 1-2 hours to capture the funding drop. You literally get paid to hold the position through the funding snapshot.

#### Improvement 3: Implement Partial Scale-Outs (The "+1R" Rule)
ASM currently likely holds for a full target (e.g., 1:2 or 1:3 R:R), which gives back profits during intraday reversals.
*   **The Idea**: Modify the State Manager (`app/core/state.py`) to handle split exits.
*   **The Execution**: When a position hits +1R (1x your initial risk in profit), automatically close 50% of the position using a Post-Only Limit order, and move the Stop Loss for the remaining 50% to Breakeven. This locks in daily cash flow and drastically improves the win rate, turning many "would-be losers" into breakevens or small wins.

---

### 📋 Summary Checklist for Your Next Commit

To fix last night's losses and improve the bot, implement these in this exact order:

1. [ ] **Risk Gate**: Add `min_notional_usd = 50.0` check to reject dust trades.
2. [ ] **Executor**: Ensure `TAKE_PROFIT` orders are strictly `Post-Only` and never fall back to `Market`.
3. [ ] **Session/Alpha**: Add an `ADX < 20` filter or a `00:00-06:00 UTC` time-block to prevent chop entries.
4. [ ] **State Manager**: Implement the 50% scale-out at +1R to lock in daily profits.

By applying these specific fixes to ASM's modular architecture, you will stop bleeding money to exchange fees and choppy markets, transforming it from a conservative bot into a highly efficient, daily profit generator.