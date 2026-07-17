# Active Position Manager (APM)
**Document:** `docs/execution/active_position_manager.md`  
**Status:** Draft — Phase 6  
**Supersedes:** `docs/plan/active_position_manager.md` (promoted from plan to authoritative spec)

---

## 1. Overview

> "Entry is only 10% of the game. Trade management is the other 90%."

Once the `BybitExecutor` fills an order and places the initial exchange-side Stop-Loss, the `ActivePositionManager` (APM) takes ownership. It runs as a **continuous 2-second async loop** inside the single `asyncio` process, monitoring every open position and making dynamic adjustments to Stop-Loss and Take-Profit orders — always via Bybit API, never just in memory.

**Without the APM, the system is a signal generator.** With the APM, it becomes a complete autonomous portfolio manager that defends capital and locks in profits.

---

## 2. The Exchange-Side Mandate

> ⚠️ **This is the single most safety-critical requirement for the APM.**

Every SL/TP amendment made by the APM **must** call the Bybit API to place or amend the actual order on Bybit's matching engine servers:

```python
# CORRECT — exchange-side amendment
await self.executor.amend_stop_loss(symbol=pos['symbol'], new_sl=new_sl_price)

# WRONG — in-memory tracking only, provides NO protection if process dies
self.state.update_sl_price(pos['id'], new_sl_price)
```

**Why this matters:** If the Docker container crashes, the VPS loses power, the Gluetun VPN disconnects, or the asyncio event loop freezes, Bybit's matching engine will still execute the exchange-side Stop-Loss. An in-memory SL provides zero protection against process death.

This mandate applies to:
- Initial SL placement (already required by Phase 5 — on every fill)
- Breakeven SL amendment (at +1R)
- ATR trailing SL amendments (on each APM cycle where price has moved favorably)
- Take-Profit placement and amendment (RANGE/CHOP fixed TP)

If `amend_stop_loss()` fails, the APM must:
1. Log a `CRITICAL`-level error to Postgres
2. Send a Telegram alert with the failed symbol and attempted SL price
3. Retry once after a 5-second delay
4. If the second attempt also fails, close the position at market (`force_close(reason="sl_amendment_failed")`)

---

## 3. APM Architecture

### 3.1 Location and Startup

```text
app/execution/position_manager.py   # ActivePositionManager class
```

The APM is started in `app/main.py` as an `asyncio.create_task()` alongside the other key tasks. It runs for the lifetime of the process.

### 3.2 Main Loop

```python
# app/execution/position_manager.py

import asyncio
from decimal import Decimal
from datetime import datetime, timezone

class ActivePositionManager:
    """
    Continuous 2-second async monitoring loop for all open positions.

    Rules:
    - All SL/TP changes MUST go through bybit_client (exchange-side).
    - Loop MUST include try/except + asyncio.sleep() on error path.
    - initial_risk_per_unit MUST be read from Postgres (set at entry, immutable).
    """

    MONITOR_INTERVAL_S = 2
    ERROR_BACKOFF_S    = 5
    RECONCILE_INTERVAL_S = 300   # 5-minute ghost position check

    def __init__(self, bybit_client, state_manager, regime_classifier, logger):
        self.executor          = bybit_client
        self.state             = state_manager
        self.regime_classifier = regime_classifier
        self.logger            = logger
        self._last_reconcile   = datetime.now(timezone.utc)

    async def start_monitoring(self):
        """Entry point — called once from main.py via asyncio.create_task()."""
        self.logger.info("ActivePositionManager started.")
        while True:
            try:
                await self._run_cycle()
                await asyncio.sleep(self.MONITOR_INTERVAL_S)

            except Exception as e:
                self.logger.error(f"APM main loop error: {e}", exc_info=True)
                # Back-off to prevent CPU starvation on repeated errors
                await asyncio.sleep(self.ERROR_BACKOFF_S)

    async def _run_cycle(self):
        """Single APM cycle: monitor all positions, then reconcile if due."""
        positions = await self.state.get_open_positions()

        for pos in positions:
            live_price = await self.executor.get_mark_price(pos['symbol'])
            await self._manage_single_position(pos, Decimal(str(live_price)))

        # Periodic ghost-position reconciliation
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_reconcile).total_seconds()
        if elapsed >= self.RECONCILE_INTERVAL_S:
            await self._reconcile_positions()
            self._last_reconcile = now
```

---

## 4. R-Multiple Calculation

The APM tracks position health using **R-Multiples** — a normalized measure of profit/loss relative to initial risk.

```python
def _calculate_r_multiple(self, pos: dict, live_price: Decimal) -> Decimal:
    """
    R = (live_price - entry_price) / initial_risk_per_unit   [LONG]
    R = (entry_price - live_price) / initial_risk_per_unit   [SHORT]

    initial_risk_per_unit = abs(entry_price - initial_sl_price)
    This value is stored in Postgres at trade entry and is IMMUTABLE.
    """
    entry_price          = Decimal(str(pos['entry_price']))
    initial_risk_per_unit = Decimal(str(pos['initial_risk_per_unit']))

    if initial_risk_per_unit == Decimal('0'):
        self.logger.error(f"initial_risk_per_unit is 0 for position {pos['id']} — cannot calculate R")
        return Decimal('0')

    if pos['side'] == 'LONG':
        return (live_price - entry_price) / initial_risk_per_unit
    else:
        return (entry_price - live_price) / initial_risk_per_unit
```

> `initial_risk_per_unit` is always `Decimal`, always set at entry, never recalculated. Zero-division is guarded.

---

## 5. Management Rules (per Position)

```python
async def _manage_single_position(self, pos: dict, live_price: Decimal):
    regime = pos['entry_regime']   # Regime at time of entry — immutable
    current_pnl_r = self._calculate_r_multiple(pos, live_price)

    # ── RULE 1: +1R BREAKEVEN LOCK ──────────────────────────────────────────
    if current_pnl_r >= Decimal('1.0') and not pos.get('moved_to_breakeven'):
        await self._move_stop_to_breakeven(pos)
        if regime in ('RANGE', 'CHOP'):
            await self._scale_out_position(pos, percentage=Decimal('50'))

    # ── RULE 2: REGIME-SPECIFIC MANAGEMENT ──────────────────────────────────
    if regime in ('TREND_BULL', 'TREND_BEAR'):
        await self._manage_trend_trailing_stop(pos, live_price, current_pnl_r)
    elif regime == 'RANGE':
        await self._manage_time_exit(pos, max_minutes=240)
    elif regime == 'CHOP':
        await self._manage_time_exit(pos, max_minutes=30)

    # ── RULE 3: REGIME SHIFT KILL SWITCH ────────────────────────────────────
    current_regime = await self.regime_classifier.get_current_regime(pos['symbol'])
    if current_regime.value != pos['entry_regime']:
        self.logger.warning(
            f"Regime shift detected for {pos['symbol']}: "
            f"{pos['entry_regime']} → {current_regime.value}. Closing position."
        )
        await self._force_close_position(pos, reason="regime_shift")
```

---

## 6. +1R Breakeven Lock

**Trigger:** `current_pnl_r >= 1.0` for the first time (guarded by `moved_to_breakeven` flag).  
**Effect:** Moves the exchange-side Stop-Loss to entry price + fee buffer (for longs) or entry - fee buffer (for shorts).  
**Additional effect for RANGE/CHOP:** Scale out 50% of the position at market.

```python
async def _move_stop_to_breakeven(self, pos: dict):
    """Amends the exchange-side SL to entry price ± fee buffer. ONCE per position."""
    entry_price  = Decimal(str(pos['entry_price']))
    fee_buffer   = entry_price * Decimal('0.001')   # 0.1% buffer covers round-trip fees

    if pos['side'] == 'LONG':
        new_sl = entry_price + fee_buffer
    else:
        new_sl = entry_price - fee_buffer

    # Always exchange-side — never in-memory only
    await self.executor.amend_stop_loss(pos['symbol'], new_sl)
    await self.state.update_position(pos['id'], moved_to_breakeven=True, current_sl=new_sl)
    self.logger.info(f"[{pos['symbol']}] Breakeven lock: SL moved to {new_sl}")
```

> `moved_to_breakeven=True` is written to Postgres and Redis immediately. On the next APM cycle, the guard condition prevents re-triggering even if the position temporarily dips below +1R before recovering.

---

## 7. ATR-Based Trailing Stop (TREND Only)

**Activates:** When `current_pnl_r >= 1.5` (position is 1.5R profitable).  
**Logic:** Chandelier Exit — SL trails 3× ATR below the highest high since entry.

```python
async def _manage_trend_trailing_stop(self, pos: dict, live_price: Decimal, current_pnl_r: Decimal):
    """3x ATR Chandelier trailing stop. Only amends if new SL is MORE protective."""
    if current_pnl_r < Decimal('1.5'):
        return   # Not yet in trailing zone

    atr           = await self._get_current_atr(pos['symbol'])
    trail_distance = atr * Decimal('3.0')
    current_sl     = Decimal(str(pos['current_sl']))

    if pos['side'] == 'LONG':
        new_trailing_sl = live_price - trail_distance
        # Only amend if the new SL is HIGHER (more protective) than current SL
        if new_trailing_sl > current_sl:
            await self.executor.amend_stop_loss(pos['symbol'], new_trailing_sl)
            await self.state.update_position(pos['id'], current_sl=new_trailing_sl)
            self.logger.info(f"[{pos['symbol']}] Trailing SL raised: {current_sl} → {new_trailing_sl}")

    elif pos['side'] == 'SHORT':
        new_trailing_sl = live_price + trail_distance
        # Only amend if the new SL is LOWER (more protective) than current SL
        if new_trailing_sl < current_sl:
            await self.executor.amend_stop_loss(pos['symbol'], new_trailing_sl)
            await self.state.update_position(pos['id'], current_sl=new_trailing_sl)
            self.logger.info(f"[{pos['symbol']}] Trailing SL lowered: {current_sl} → {new_trailing_sl}")
```

---

## 8. Time-Based Exits (RANGE and CHOP)

```python
async def _manage_time_exit(self, pos: dict, max_minutes: int):
    """Closes the trade if it exceeds the maximum allowed hold time."""
    entry_time   = pos['entry_timestamp']           # datetime with tzinfo=UTC
    now          = datetime.now(timezone.utc)
    minutes_held = (now - entry_time).total_seconds() / 60.0

    if minutes_held > max_minutes:
        self.logger.info(
            f"[{pos['symbol']}] Time exit: {minutes_held:.1f}min > {max_minutes}min limit."
        )
        await self._force_close_position(pos, reason="time_exit")
```

| Regime | Max Hold Time |
|:---|:---|
| TREND | 24h (1440 min) — via trailing stop, not time exit |
| RANGE | 4h (240 min) |
| CHOP | 30min |

---

## 9. Regime Shift Kill Switch

> **This is the most important adaptive mechanism in the APM.** It prevents a trend trade from slowly bleeding out when the market transitions to range or chop.

**Logic:** On every APM cycle, the current market regime is fetched from `RegimeClassifier`. If it differs from `pos['entry_regime']` (the regime at the time the trade was entered), the position is closed at market immediately.

```python
# In _manage_single_position():
current_regime = await self.regime_classifier.get_current_regime(pos['symbol'])
if current_regime.value != pos['entry_regime']:
    await self._force_close_position(pos, reason="regime_shift")
```

**Why this works:** A TREND_BULL trade entered because ADX > 25 and momentum was strong. If ADX has dropped to 18 two hours later, the trend is over. The original thesis is dead. A wide trend SL will get hit. Better to close for a small scratch loss or small profit than wait for the full SL.

**The Regime Shift Kill Switch CANNOT be:**
- Disabled via config
- Made "optional" (soft warning instead of close)
- Delayed (the close happens on the *same cycle* the shift is detected)

---

## 10. Force Close Sequence

```python
async def _force_close_position(self, pos: dict, reason: str):
    """
    Market closes the position and cancels all attached TP/SL orders.
    This is the final step for time_exit, regime_shift, and sl_amendment_failed.
    """
    symbol   = pos['symbol']
    quantity = Decimal(str(pos['quantity']))

    self.logger.warning(f"[{symbol}] Force closing: reason={reason}")

    # Step 1: Cancel all open orders for this symbol (removes TP/SL from exchange)
    await self.executor.cancel_all_orders(symbol)

    # Step 2: Market close the position
    await self.executor.place_market_close(symbol, quantity)

    # Step 3: Update local state
    await self.state.close_position(pos['id'], reason=reason)

    # Step 4: Alert operator
    await self.alert_service.send(
        f"🔴 Position closed: {symbol} | Reason: {reason} | Size: {quantity}"
    )
```

---

## 11. Ghost Position Reconciliation

Every 5 minutes, the APM compares its internal open positions against Bybit REST API actual positions.

```python
async def _reconcile_positions(self):
    """
    Detects and corrects 'ghost positions' — positions the bot thinks are open
    but Bybit has already closed (via exchange SL, liquidation, or missed event).
    """
    internal_positions = await self.state.get_open_positions()
    bybit_positions    = await self.executor.get_positions()

    bybit_symbols = {p['symbol'] for p in bybit_positions if p['size'] > 0}

    for pos in internal_positions:
        if pos['symbol'] not in bybit_symbols:
            self.logger.critical(
                f"Ghost position detected: {pos['symbol']} is open in DB "
                f"but Bybit shows FLAT. Correcting local state."
            )
            await self.state.close_position(pos['id'], reason="ghost_reconciliation")
            await self.alert_service.send(
                f"⚠️ Ghost position corrected: {pos['symbol']} was flat on Bybit "
                f"but open in local DB. State synced."
            )
```

> This is the APM's version of the "Trust Nothing" reconciliation principle that governs startup. It extends the principle to the runtime lifecycle.

---

## 12. Prometheus Metrics (New — Phase 6)

> Proposed additions — flag against `docs/METRICS_DICTIONARY.md` before implementing.

| Metric Name | Type | Labels | Description |
|:---|:---|:---|:---|
| `asm_apm_cycle_duration_seconds` | Histogram | — | APM cycle wall-clock time |
| `asm_breakeven_locks_total` | Counter | `symbol`, `regime` | Breakeven moves applied |
| `asm_trailing_sl_amendments_total` | Counter | `symbol` | Exchange-side SL trailing amendments |
| `asm_regime_shift_exits_total` | Counter | `from_regime`, `to_regime` | Positions closed due to regime shift |
| `asm_time_exits_total` | Counter | `regime` | Positions closed due to time limit |
| `asm_ghost_positions_corrected_total` | Counter | — | Ghost position reconciliation events |
| `asm_sl_amendment_failures_total` | Counter | `symbol` | Failed exchange-side SL amendments (triggers force-close) |

---

## 13. State Fields Required in Postgres `trades` Table

> Cross-reference with `docs/DATA_MODEL.md`. These are proposed additions — verify against current DDL.

| Field | Type | Description |
|:---|:---|:---|
| `entry_regime` | `VARCHAR(20)` | MarketRegime value at entry (immutable) |
| `initial_risk_per_unit` | `NUMERIC(20,8)` | `abs(entry_price - initial_sl_price)` — set at fill, never modified |
| `moved_to_breakeven` | `BOOLEAN` | `False` until APM fires the +1R lock, then permanently `True` |
| `current_sl` | `NUMERIC(20,8)` | Most recent exchange-side SL price (updated by APM) |
| `risk_profile_json` | `JSONB` | Serialized `RiskProfile` for this position |
