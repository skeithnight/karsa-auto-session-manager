This is the most critical phase of algorithmic trading. **Entry is only 10% of the game; Trade Management (the "after") is the other 90%.** 

Once the Bybit Executor fills the order, the bot must transition into an **Active Position Manager (APM)**. This module runs continuously in the background, monitoring the open position and dynamically adjusting Stop Losses (SL), Take Profits (TP), and executing exits based on the rules of the specific regime you are in.

Here is the exact blueprint for how to secure your capital and lock in profits *after* execution.

---

### 🏗️ The Architecture: The Active Position Manager (APM)

You need a dedicated async loop (e.g., `app/execution/position_manager.py`) that runs every 1–3 seconds. It listens to the WebSocket price feed and compares the live price against the **Dynamic Risk Profile** assigned at entry.

**Crucial ASM Rule:** The APM must place and amend SL/TP orders **directly on the Bybit exchange servers**. Never rely solely on the bot's internal memory to trigger an exit. If the bot crashes, Bybit must still protect you.

---

### 🛡️ Part 1: Regime-Specific Trade Management Rules

How you manage the trade depends entirely on the regime you entered in.

#### 1. TREND Regime Management (Ride the Wave)
*Goal: Capture massive moves, survive normal pullbacks.*
*   **Initial SL:** Wide. Placed below the most recent "swing low" (for longs) or above the "swing high" (for shorts), plus 1x ATR (Average True Range) buffer to avoid wick-hunts.
*   **Take Profit:** No fixed TP. Use a **Trailing Stop**.
*   **Profit Securing (Scale-Out):** When the trade hits **+2R** (profit is 2x the initial risk), close **30% of the position** and move the SL for the remaining 70% to **Breakeven (Entry Price + Fees)**.
*   **Trailing Logic:** As price moves in your favor, trail the SL behind the price using a "Chandelier Exit" (e.g., 3x ATR below the highest high since entry).

#### 2. RANGE Regime Management (Ping-Pong)
*Goal: High win rate, quick profits, strict invalidation.*
*   **Initial SL:** **Very Tight.** Placed exactly 0.1% to 0.2% outside the Bollinger Band or support/resistance edge. If the price breaks the edge, the thesis is wrong. Get out immediately.
*   **Take Profit:** **Fixed.** Placed at the opposite side of the range, or at the VWAP (Volume Weighted Average Price) in the middle of the range.
*   **Profit Securing:** When the trade hits **+1R**, close **50% of the position** and move the SL to Breakeven. 
*   **Time Exit:** If the price hasn't hit TP or SL within **4 hours**, close the position at market. Range trades should work immediately; if they stall, the range is likely breaking.

#### 3. CHOP Regime Management (Micro-Scalp)
*Goal: Get in, grab a tiny piece of liquidity, get out.*
*   **Initial SL:** Micro. Placed just beyond the immediate local liquidity wick.
*   **Take Profit:** Fixed at a quick **1:1 Risk/Reward**.
*   **Time Exit:** **30 minutes max.** If it's not working instantly, kill it.

---

### 💻 Part 2: Implementing the Position Manager (Code)

Here is how you code the APM to handle these rules dynamically.

```python
# app/execution/position_manager.py
import asyncio
from decimal import Decimal

class ActivePositionManager:
    def __init__(self, bybit_executor, state_manager, logger):
        self.executor = bybit_executor
        self.state = state_manager
        self.logger = logger
        self.active_positions = {}

    async def start_monitoring(self):
        """Main async loop running every 2 seconds."""
        self.logger.info("Position Manager started.")
        while True:
            try:
                # 1. Get all open positions from local state
                positions = await self.state.get_open_positions()
                
                for pos in positions:
                    live_price = await self.executor.get_mark_price(pos['symbol'])
                    await self._manage_single_position(pos, live_price)
                    
                await asyncio.sleep(2) # Check every 2 seconds
                
            except Exception as e:
                self.logger.error(f"Position Manager loop error: {e}")
                await asyncio.sleep(5)

    async def _manage_single_position(self, pos, live_price: Decimal):
        """Applies regime-specific rules to an open position."""
        regime = pos['entry_regime']
        side = pos['side']
        entry_price = pos['entry_price']
        initial_risk = pos['initial_risk_amount'] # e.g., distance to initial SL
        
        # Calculate current R-Multiple (How many 'R' are we in profit/loss?)
        if side == 'LONG':
            current_pnl_r = (live_price - entry_price) / initial_risk
        else:
            current_pnl_r = (entry_price - live_price) / initial_risk

        # --- RULE 1: THE "+1R BREAKEVEN" LOCK (Universal Profit Securer) ---
        if current_pnl_r >= 1.0 and not pos.get('moved_to_breakeven'):
            await self._move_stop_to_breakeven(pos, entry_price)
            # Optional: Scale out 50% here for Range/Chop regimes
            if regime in ['RANGE', 'CHOP']:
                await self._scale_out_position(pos, percentage=50)

        # --- RULE 2: REGIME-SPECIFIC TRAILING / EXITS ---
        if regime == 'TREND':
            await self._manage_trend_trailing_stop(pos, live_price, current_pnl_r)
            
        elif regime == 'RANGE':
            await self._manage_time_exit(pos, max_minutes=240)
            
        elif regime == 'CHOP':
            await self._manage_time_exit(pos, max_minutes=30)

        # --- RULE 3: REGIME SHIFT KILL SWITCH (The Ultimate Protection) ---
        current_market_regime = await self.regime_classifier.get_current_regime(pos['symbol'])
        if current_market_regime != pos['entry_regime']:
            # The market personality changed! The original thesis is invalid.
            self.logger.warning(f"Regime shift detected for {pos['symbol']}. Closing position.")
            await self._force_close_position(pos, reason="regime_shift")

    async def _move_stop_to_breakeven(self, pos, entry_price):
        """Moves the exchange-side Stop Loss to the entry price + fees."""
        fee_buffer = pos['entry_price'] * Decimal('0.001') # 0.1% buffer for fees
        if pos['side'] == 'LONG':
            new_sl = entry_price + fee_buffer
        else:
            new_sl = entry_price - fee_buffer
            
        await self.executor.amend_stop_loss(pos['symbol'], new_sl)
        await self.state.update_position(pos['id'], moved_to_breakeven=True)
        self.logger.info(f"Moved {pos['symbol']} SL to Breakeven: {new_sl}")

    async def _manage_trend_trailing_stop(self, pos, live_price, current_pnl_r):
        """Trails the stop loss using a 3x ATR Chandelier exit."""
        if current_pnl_r > 1.5: # Only start trailing after 1.5R profit
            atr = await self.get_current_atr(pos['symbol'])
            trail_distance = atr * Decimal('3.0')
            
            if pos['side'] == 'LONG':
                new_trailing_sl = live_price - trail_distance
                # Only amend if the new SL is HIGHER than the current SL
                if new_trailing_sl > pos['current_sl']:
                    await self.executor.amend_stop_loss(pos['symbol'], new_trailing_sl)
            # (Add inverse logic for SHORT)

    async def _manage_time_exit(self, pos, max_minutes):
        """Closes the trade if it takes too long."""
        entry_time = pos['entry_timestamp']
        minutes_held = (datetime.utcnow() - entry_time).total_seconds() / 60
        
        if minutes_held > max_minutes:
            self.logger.info(f"Time exit triggered for {pos['symbol']} after {minutes_held} mins.")
            await self._force_close_position(pos, reason="time_exit")

    async def _force_close_position(self, pos, reason):
        """Market closes the position and cancels attached TP/SL orders."""
        await self.executor.cancel_all_orders(pos['symbol'])
        await self.executor.place_market_close(pos['symbol'], pos['quantity'])
        await self.state.close_position(pos['id'], reason=reason)
```

---

### 🔒 Part 3: The 3 "Unbreakable" Safety Rules for the APM

To ensure this system actually secures your money and doesn't introduce new bugs, you must enforce these three rules in your code:

#### 1. The "Exchange-Side" Mandate
When the APM calculates a new Stop Loss or Take Profit, it **must** send an API request to Bybit to place/amend the actual order (`pybit` or `ccxt` `create_order` with `stopLoss`/`takeProfit` parameters). 
*   *Why?* If your Docker container crashes, your VPS loses power, or the Gluetun VPN disconnects, Bybit's matching engine will still execute your Stop Loss and save your account from liquidation.

#### 2. The "Regime Shift" Kill Switch
Notice the code block: `if current_market_regime != pos['entry_regime']`. 
This is the secret weapon of an Adaptive Bot. If you enter a LONG in a `TREND_BULL` regime, but 2 hours later the ADX drops and the classifier says the market is now `RANGE`, **your trend thesis is dead**. The bot immediately closes the trade at market, taking a small scratch loss or tiny profit, rather than waiting for a wide trend stop-loss to get hit.

#### 3. The "Trust Nothing" Reconciliation
Every 5 minutes, the APM must run a background check comparing its internal `self.active_positions` dictionary with the actual open positions returned by the Bybit API (`executor.get_positions()`). 
*   If the bot thinks it has a position, but Bybit says it was stopped out (due to a missed WebSocket tick or slippage), the APM must instantly update the local database to prevent the bot from trying to manage a "ghost" position.

---

### 💡 Summary of the Post-Execution Flow

1.  **Entry:** Executor fills order. Assigns `RiskProfile` based on Regime. Places initial SL and TP on Bybit servers.
2.  **Monitoring:** APM loop checks price every 2 seconds. Calculates live R-Multiple.
3.  **Securing Profit:** At +1R, APM moves SL to Breakeven on Bybit. (For Range/Chop, it also sells 50% of the position).
4.  **Maximizing Profit:** If Trend regime, APM trails the SL behind the price using ATR.
5.  **Cutting Losses:** If time-exit is reached, or if the market Regime changes, APM market-closes the position immediately.
6.  **Safety:** All SL/TP orders live on Bybit's servers. Internal state is constantly reconciled.