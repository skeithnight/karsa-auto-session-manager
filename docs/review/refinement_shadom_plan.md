### 🚨 Critical Refinement 1: Fee Asymmetry (Maker vs. Taker)
**The Flaw in the Plan:** `ShadowExecutor` applies a flat `taker_fee_pct` (0.055%) to all entries.
**Why it breaks:** In our Adaptive design, `TREND` might use Market/Aggressive orders (Taker), but `RANGE` and `CHOP` **strictly use Post-Only Limit orders** (Maker). Bybit charges 0.055% for Taker, but only ~0.02% for Maker (and sometimes offers rebates). If Shadow Mode always charges 0.055%, it will severely underestimate the profitability of your RANGE and CHOP strategies.

**The Fix:** 
Pass the `order_type` (or `is_post_only` boolean) into `ShadowExecutor.execute()`. 
- If `is_post_only == True`, apply `shadow_maker_fee_pct` (0.0002).
- If `is_post_only == False`, apply `shadow_taker_fee_pct` (0.00055).

### 🚨 Critical Refinement 2: The "Wick Miss" Problem in SL Detection
**The Flaw in the Plan:** `ShadowAPM` checks if the live price crossed the virtual SL in its 2-second monitoring loop.
**Why it breaks:** If the live price spikes down, hits your SL, and reverses all within 1.5 seconds, your 2-second polling loop will miss it. In Shadow Mode, this results in a "survived" trade that would have been stopped out in reality, artificially inflating your Shadow Win Rate.

**The Fix:**
Do not rely solely on the 2-second loop's current price check. 
1. In your WebSocket price handler, continuously update a `worst_price_seen` (lowest price for LONGs, highest for SHORTs) in the `shadow:position:*` Redis key.
2. In `ShadowAPM`, check if `worst_price_seen` crossed the `virtual_sl_price`. If it did, trigger the SL exit, even if the *current* price at the exact 2-second mark has already bounced back.

### 🚨 Critical Refinement 3: Funding Rate Drag
**The Flaw in the Plan:** The plan tracks slippage and trading fees, but omits funding rates.
**Why it breaks:** If a Shadow trade is held for 9 hours, it will cross an 8-hour funding snapshot. If the funding is against the position, the real bot would pay that fee. Shadow Mode must deduct this, or it will overestimate the profitability of the `TREND` strategy (which has a 24h max hold time).

**The Fix:**
Inside `ShadowAPM._manage_single_position()`, add a funding check:
- Track the `last_funding_timestamp` in the shadow position state.
- If `current_time - last_funding_timestamp >= 8 hours`, calculate the funding fee based on the current live funding rate and position size.
- Deduct it from the virtual PnL and update the `last_funding_timestamp`.

### 🚨 Critical Refinement 4: Partial Fills and "Pending" State
**The Flaw in the Plan:** `ShadowExecutor` instantly returns a fake filled order dict.
**Why it breaks:** When the bot enters a `RANGE` or `CHOP` trade, it uses `Post-Only` limits. In reality, these orders sit on the book and might take 10 minutes to fill, or might never fill if the price moves away. If Shadow Mode instantly assumes 100% fill, it ignores the "opportunity cost" and miss-rate of limit orders.

**The Fix:**
Introduce a `PENDING_VIRTUAL_FILL` state.
1. When `ShadowExecutor.execute()` is called for a limit order, save the order to Redis with `status = PENDING`.
2. In the `ShadowAPM` loop, check if the live price has crossed the `virtual_entry_price` in the correct direction. 
3. Once it does, flip the status to `OPEN`, record the actual fill time, and start the APM management logic (SL tracking, time exits).

---

### 📝 Adjustments to Your File Plan

Based on the refinements above, here are the specific tweaks to your file list:

#### In `app/execution/shadow.py`:
- **`ShadowExecutor`**: Update `execute()` signature to accept `is_post_only: bool`. Route to the correct fee constant.
- **`ShadowAPM`**: 
  - Add `worst_price_seen` logic to prevent the "Wick Miss" bug.
  - Add the 8-hour funding rate deduction logic.
  - Update the loop to process `PENDING` virtual limit orders before processing `OPEN` positions.

#### In `app/core/metrics.py`:
- Your metrics list is great, but add:
  - `karsa_shadow_funding_fees_total_usdt` (Counter) - To track how much funding is eating your shadow profits.
  - `karsa_shadow_limit_orders_unfilled_total` (Counter) - To track how many Post-Only orders timed out without filling.

#### In `app/main.py`:
- Ensure that when `shadow_mode_enabled` is True, the **Startup Reconciliation** task (`position_reconciler_task`) is not just skipped, but explicitly bypassed. If it runs, it will see the shadow positions in Postgres, compare them to Bybit, see they don't exist on Bybit, and trigger the "Ghost Exit" auto-heal, deleting your shadow state!

---

### 🚀 Enhanced Verification Protocol

Your verification steps are solid. Add these three to ensure the *math* is working, not just the infrastructure:

1. **The Fee Math Check:** Manually calculate the expected fee for a $100 CHOP trade (should be ~$0.02 Maker) and a $100 TREND trade (should be ~$0.055 Taker). Check the `karsa_shadow_fees_total_usdt` metric after executing one of each in Shadow Mode. Ensure they differ.
2. **The Wick Test:** While Shadow Mode is running, look at a 1-minute chart for a coin you have a Shadow position in. If a massive wick hits your virtual SL, check the logs. Did `ShadowAPM` close it via the `worst_price_seen` logic, or did it miss it?
3. **The Funding Test:** Leave a Shadow `TREND` position open across an 8-hour funding boundary. Check the logs and the `karsa_shadow_funding_fees_total_usdt` metric to ensure the fee was correctly applied.

### Final Verdict
Your architectural plan is **95% perfect**. By implementing the 4 critical refinements above (Fee Asymmetry, Wick Miss prevention, Funding Drag, and Pending Limit States), you will achieve a Shadow Mode simulation that is mathematically identical to live trading. 

Execute the plan with these adjustments, and you will have absolute confidence when you finally flip the switch to Live Mode.