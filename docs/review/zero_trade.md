### 🔍 Phase 1: Interrogate the Risk Gate (The 5 Common Blockers)

If the Risk Gate is functioning as a "fail-closed" system, it will reject an order if *any* single condition is not met. Check your logs and state for these specific architectural traps:

#### 1. The "Ghost Position" Block (Max Concurrent Positions)

- **The Trap:** The bot’s local state (Postgres/Redis) believes it already has the maximum number of open positions (e.g., 3/3), even if the exchange shows 0.
- **Why it happens:** A previous crash recovery failed to clear a "closed" position in the local database, or the reconciliation phase marked an exchange position as "local" incorrectly.
- **How to verify:** Check your local database for "open" intents that do not exist on Bybit.

#### 2. The "Math & Rounding" Block (Position Sizing)

- **The Trap:** The calculated order quantity is being rounded down to `0.000` by the exchange-specific tick/lot size logic, causing the Risk Gate to reject it as "invalid size."
- **Why it happens:** The account balance is too small for the minimum notional value, OR the `Decimal` precision logic is truncating the quantity to zero before it even reaches the gate.
- **How to verify:** Log the *exact* raw calculated quantity and the *rounded* quantity right before the Risk Gate evaluates it.

#### 3. The "Stale Price" Block (Slippage Tolerance)

- **The Trap:** The Risk Gate compares the Strategy Router's intended entry price against the current Mark Price. If the difference exceeds the allowed slippage (e.g., 0.5%), it rejects the trade.
- **Why it happens:** The Market Data Ingestor is lagging. The Alpha Bridge calculated a signal based on a price of $60,000, but by the time it hits the Risk Gate 2 seconds later, the Mark Price is $60,500. The deviation triggers the slippage kill-switch.
- **How to verify:** Check the timestamp delta between the `OrderRequest` creation and the Risk Gate evaluation. If it's > 500ms, your data pipeline is too slow for your slippage tolerance.

#### 4. The "Atomic Order" Block (Missing SL/TP)

- **The Trap:** If you implemented the architectural fix from the first audit (requiring SL/TP to be attached atomically), the Risk Gate will now hard-reject any order that doesn't have valid `stop_loss` and `take_profit` prices attached.
- **Why it happens:** The Strategy Router is generating the entry price but failing to calculate or attach the exit prices before passing the request to the gate.
- **How to verify:** Check the `OrderRequest` payload at the exact moment it enters the Risk Gate. Are the SL/TP fields `null` or `0`?

#### 5. The "Circuit Breaker Hangover"

- **The Trap:** The daily drawdown limit or a symbol-specific circuit breaker was triggered in a previous session, and the Redis key was never cleared.
- **Why it happens:** The circuit breaker persistence logic (which we audited earlier) successfully wrote the `HALT` state to Redis, but the daily reset cron job failed to clear it.
- **How to verify:** Query Redis directly for the circuit breaker keys. Are they stuck in the `OPEN` state?

---

### 🕵️ Phase 2: Debugging the "Silent Swallow"

The most dangerous architectural flaw in a Risk Gate is **silent failure**. If the execution loop looks like this:

```text
1. Generate Signal
2. Pass to Risk Gate
3. If Risk Gate returns False -> Log "Trade skipped" and continue loop
```

You will never know *why* it was skipped. You must enforce **Explicit Rejection Logging**.

**The Diagnostic Checklist:**

1. **Elevate Log Levels:** Temporarily set the `app/risk/` and `app/execution/` modules to `DEBUG`.
2. **Trace the Handoff:** You need to see the exact lifecycle of the `OrderRequest` object.
   - *Did it leave the Strategy Router?*
   - *Did it enter the Risk Gate?*
   - *Which specific `if` statement inside the Risk Gate evaluated to `False` or raised an Exception?*
3. **Check for Swallowed Exceptions:** Look at the Smart Order Router (SOR). If the SOR wraps the Risk Gate call in a broad `try/except Exception:` block and only logs it as a `warning` or `debug`, the actual error (e.g., a missing dictionary key, a Decimal type mismatch) is being hidden from you.

---

### 🌉 Phase 3: Verify the Exchange Handoff (Post-Risk Gate)

If you are 100% certain the Risk Gate is passing the trade (returning `True`), but it *still* doesn't execute, the failure is happening in the milliseconds *after* the gate.

1. **API Permissions / IP Whitelist:** Does the Bybit API key actually have "Trade" permissions enabled? Is the server's IP (or your WireGuard `gluetun` exit IP) whitelisted on Bybit?
2. **The "Dry Run" Flag:** Is there a configuration flag in your `.env` or config file that accidentally sets the bot to `PAPER_TRADING=true` or `DRY_RUN=true`, causing the SOR to simulate the order instead of sending it to the exchange?
3. **Rate Limit Exhaustion:** Is the bot hitting Bybit's API weight limits *before* the order is sent? If the `watch_orders` websocket is consuming all your API weight, the REST call to `place_order` will silently fail or return a 429 error.

---

### 🚀 Your Immediate Action Plan

To find the exact blocker without guessing, do this right now:

1. **Open your logs** and filter for the exact string the Risk Gate uses when it rejects a trade (e.g., "Order rejected", "Risk check failed", "Skipping trade").
2. **Look at the 3 log lines immediately preceding that rejection.** The data required to understand *why* it was rejected is almost always logged right before the drop.
3. **If there are no logs**, your Risk Gate is failing silently. You must locate the `validate_order` (or similarly named) function in your codebase and add a `logger.error` statement at every single `return False` or `raise Exception` branch.
