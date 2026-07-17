This is a classic and highly critical issue in algorithmic trading known as the **"Ghost Exit" or "State Desync" problem**.

### 🔍 The Root Cause: Why is this happening?

Your bot is placing Stop-Loss (SL) and Take-Profit (TP) orders directly on Bybit's servers (which is the correct safety practice). When the price hits those levels, **Bybit's matching engine closes the position instantly.**

However, your bot's local database (PostgreSQL/Redis) still thinks the trade is open. This happens because:

1. **WebSocket Drop/Lag:** During the exact millisecond the SL/TP triggers, there is a micro-spike in volume. The Bybit WebSocket might drop the `execution` or `position` update packet, or your bot's WS handler was busy processing another tick and missed it.
2. **Process Restart:** If the bot crashed or restarted right as the trade was closing, the local state wasn't saved to the database.
3. **Reconciliation Logic Flaw:** Your current reconciliation script is designed to be "dumb." It sees a mismatch and throws a `🔴 missing_exit` alert, but it **doesn't actually fix the local database**. It just waits for you to fix it manually.

### ⚠️ The Danger of "Ghost Exits"

If your local database thinks Trade #349 (BSB) is still open, but Bybit has already closed it:

- Your **Portfolio Risk Manager** will calculate your exposure incorrectly (thinking you have more margin used than you actually do).
- The **Active Position Manager (APM)** might try to send a "Close Position" market order for BSB again, resulting in an accidental reverse position (opening a Short when you meant to close a Long).
- Your daily PnL calculations will be completely wrong.

---

### 🛠️ How to Fix It (3-Step Architecture Upgrade)

We need to upgrade your State Manager and Reconciliation Engine from "Alert Only" to **"Auto-Heal & Prevent"**.

#### Fix 1: Implement "Auto-Healing" Reconciliation (Immediate Fix)

Stop throwing manual alerts for missing exits. If Bybit says it's closed, **Bybit is the source of truth**. The bot must automatically fetch the exit data and update the local database.

Update your reconciliation script (likely in `app/core/state.py` or `app/watchdog/reconciliation.py`):

```python
# app/core/state.py (Inside the reconciliation loop)

async def reconcile_closed_trades(self):
    # 1. Get all trades that are "OPEN" in local DB
    local_open_trades = await self.db.get_open_trades()
    
    # 2. Get actual open positions from Bybit REST API
    bybit_open_positions = await self.executor.get_open_positions()
    bybit_open_symbols = {pos['symbol'] for pos in bybit_open_positions}
    
    for trade in local_open_trades:
        symbol = trade['symbol']
        
        # 3. The "Ghost Exit" Check: Local says open, Bybit says closed
        if symbol not in bybit_open_symbols:
            self.logger.warning(f"🔴 Ghost Exit detected for {symbol} (Local ID: {trade['id']}). Auto-healing...")
            
            # 4. Fetch the actual closed trade history from Bybit to get the exit data
            closed_history = await self.executor.get_closed_trades(symbol, limit=10)
            
            # Find the matching exit record (match by side, qty, and approximate time)
            exit_record = self._match_exit_record(trade, closed_history)
            
            if exit_record:
                # 5. AUTO-HEAL: Update the local database with the real exit data
                await self.db.update_trade_exit(
                    trade_id=trade['id'],
                    exit_price=Decimal(exit_record['avg_exit_price']),
                    closed_pnl=Decimal(exit_record['closed_pnl']),
                    exit_timestamp=exit_record['created_time'],
                    status='CLOSED'
                )
                self.logger.info(f"🟢 Auto-healed {symbol} (Local ID: {trade['id']}). PnL: {exit_record['closed_pnl']}")
            else:
                self.logger.error(f"❌ CRITICAL: Could not find exit record on Bybit for {symbol}. Manual review required.")
```

#### Fix 2: The "Safety Net" REST Poller (Root Cause Prevention)

You cannot rely 100% on WebSockets for position closures. WebSockets are fast but unreliable for state synchronization. You need a lightweight REST fallback inside your **Active Position Manager (APM)**.

Modify `app/execution/position_manager.py` to periodically verify position status via REST:

```python
# app/execution/position_manager.py

class ActivePositionManager:
    def __init__(self, ...):
        self.rest_check_counter = 0

    async def start_monitoring(self):
        while True:
            positions = await self.state.get_open_positions()
            
            for pos in positions:
                # Every 5 loops (e.g., every 10 seconds if loop is 2s), do a REST check
                self.rest_check_counter += 1
                if self.rest_check_counter >= 5:
                    await self._verify_position_via_rest(pos)
                    
                live_price = await self.executor.get_mark_price(pos['symbol'])
                await self._manage_single_position(pos, live_price)
                
            if self.rest_check_counter >= 5:
                self.rest_check_counter = 0 # Reset counter
                
            await asyncio.sleep(2)

    async def _verify_position_via_rest(self, pos):
        """Safety net: Checks Bybit REST API to ensure the position actually exists."""
        try:
            bybit_pos = await self.executor.get_position_by_symbol(pos['symbol'])
            
            if bybit_pos is None or float(bybit_pos['size']) == 0.0:
                # The position is gone on Bybit, but local DB thinks it's open!
                self.logger.warning(f"REST Safety Net: {pos['symbol']} closed on exchange but missed by WS. Triggering auto-heal.")
                await self.state.reconcile_closed_trades() # Trigger the auto-heal logic immediately
                
        except Exception as e:
            self.logger.error(f"REST check failed for {pos['symbol']}: {e}")
```

#### Fix 3: WebSocket Resilience & "Reduce-Only" Enforcement

Ensure that when your bot places SL/TP orders, they are strictly marked as **Reduce-Only**.
If a WebSocket drops and the bot gets confused, a non-reduce-only order might accidentally open a new position instead of closing the old one.

Check your `app/execution/bybit_executor.py`:

```python
async def place_stop_loss(self, symbol, qty, stop_price, side):
    params = {
        "reduce_only": True,  # CRITICAL: Prevents accidental reverse positions
        "close_on_trigger": True, # Bybit specific: ensures it only closes existing pos
        "order_type": "Market",
        # ... other params
    }
    return await self.client.place_order(symbol, side, params)
```

---

### 📋 Summary of Actions for Your Next Commit

1. **Upgrade the Reconciler:** Change the `missing_exit` logic from "Log Alert" to "Fetch Bybit History -> Update Local DB -> Log Success". (Fix 1)
2. **Add the REST Safety Net:** Implement the periodic REST position check in the APM to catch WS drops in real-time. (Fix 2)
3. **Verify Reduce-Only:** Audit your SL/TP execution code to ensure `reduce_only=True` is strictly enforced. (Fix 3)

By implementing the **Auto-Healing Reconciliation**, you will never get the `🔴 missing_exit` alert again. The bot will silently fix its own brain when it misses a WebSocket packet, keeping your local state perfectly synced with Bybit's reality.
