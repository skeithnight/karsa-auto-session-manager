## ⚠️ Part 1: The Reality of "Always Profitable"

In quantitative trading, **"always profitable" is a mathematical impossibility** due to market stochasticity, slippage, and black swan events. Any system claiming this is either overfitted, lying, or taking hidden tail-risk (e.g., martingale) that will eventually blow up the account.

Instead, the engineering goal must be reframed to: **Maximize Positive Expected Value (+EV) while driving the Risk of Ruin (RoR) to absolute zero.**

### The Formula for Long-Term Profitability

1. **Positive Expectancy**: `(Win Rate × Average Win) - (Loss Rate × Average Loss) > 0`
2. **Asymmetric Risk/Reward**: Never risk more than 1-2% of total equity on a single trade. A 50% win rate with a 1:2 risk/reward ratio is highly profitable.
3. **Capital Preservation**: The primary job of the bot is not to make money, but to *protect the capital* so it can compound. This is where the **Risk Gate** becomes the most critical component of your codebase.

---

## 🛡️ Part 2: Pre-Execution Risk Gate Architecture

Before *any* order reaches the Smart Order Router (SOR) or the Bybit client, it must pass through a strict, synchronous (or strictly awaited) `RiskEngine`. This gate must be **fail-closed** (if the check fails or times out, the trade is rejected).

### The 5-Layer Risk Gate Checklist

#### Layer 1: System & Connection State

- [ ] **Circuit Breaker Status**: Is the global or symbol-specific circuit breaker open? (e.g., daily drawdown > 5%).
- [ ] **API Health**: Is the Bybit WebSocket `watch_orders` stream active and receiving heartbeats within the last 15 seconds?
- [ ] **Rate Limit Headroom**: Do we have sufficient API weight remaining to place the order *and* the subsequent SL/TP modifications?

#### Layer 2: Account & Portfolio Level

- [ ] **Available Margin**: Does `free_balance` cover the required margin + a 20% buffer for slippage and fees?
- [ ] **Max Concurrent Positions**: Are we already at the maximum allowed open positions (e.g., max 3 symbols)?
- [ ] **Correlation Check**: (Advanced) Are we about to open a LONG on BTC and a LONG on ETH simultaneously, violating portfolio-level risk limits?

#### Layer 3: Strategy & Session Level

- [ ] **Daily Loss Limit**: Has the realized + unrealized PnL for the current session breached the max daily loss threshold?
- [ ] **Max Drawdown**: Is the current equity below the hard-stop drawdown percentage (e.g., 15% from peak)?

#### Layer 4: Order-Level Sanity Checks

- [ ] **Position Sizing**: Is the calculated `order_qty` strictly ≤ X% of total account equity? (Enforced via `Decimal` math).
- [ ] **Slippage Tolerance**: Is the requested limit price within Y% of the current Mark Price? (Prevents fat-finger errors or stale data execution).
- [ ] **Min/Max Notional**: Does the order meet Bybit’s minimum notional value (e.g., $50) and maximum position limits?

#### Layer 5: Atomic Risk Attachment (CRITICAL)

- [ ] **Embedded SL/TP**: The risk gate must *require* that `stop_loss` and `take_profit` parameters are generated and attached to the **initial** order payload. No "place order, then place SL" logic.

### 💻 Python Implementation Pattern for Karsa

```python
class RiskGateError(Exception): pass

async def validate_order_request(request: OrderRequest, state: SystemState) -> None:
    """Fail-closed risk gate. Raises exception if any check fails."""
    
    # 1. Circuit Breaker
    if state.circuit_breaker.is_open(request.symbol):
        raise RiskGateError(f"Circuit breaker OPEN for {request.symbol}")
        
    # 2. Position Sizing (Max 2% of equity)
    max_allowed_qty = (state.equity * Decimal('0.02')) / request.mark_price
    if request.quantity > max_allowed_qty:
        raise RiskGateError(f"Position size {request.quantity} exceeds 2% equity limit")
        
    # 3. Slippage Check (Max 0.5% deviation from mark price)
    price_deviation = abs(request.price - request.mark_price) / request.mark_price
    if price_deviation > Decimal('0.005'):
        raise RiskGateError(f"Slippage tolerance exceeded: {price_deviation:.4%}")
        
    # 4. Atomic SL/TP Requirement
    if not request.stop_loss_price or not request.take_profit_price:
        raise RiskGateError("CRITICAL: Order rejected. Stop Loss and Take Profit are mandatory.")
```

---

## 🔒 Part 3: Securing Profit and Loss (PnL)

"Securing PnL" means ensuring that profits are accurately calculated, protected from market reversals, and immune to software/accounting errors.

### 1. Atomic Trade Execution (The Ultimate PnL Security)

As noted in the previous audit, the biggest threat to PnL is a filled order with no Stop Loss.
**Fix:** Use Bybit’s Unified Trading API capability to send the entry, SL, and TP in a **single HTTP request**.

```python
# Bybit V5 API allows attaching TP/SL to the initial place_order call
payload = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "qty": "0.01",
    "price": "65000",
    "stopLoss": "64000",   # Attached atomically
    "takeProfit": "67000", # Attached atomically
    "tpslMode": "Full"
}
```

*Why this secures PnL:* Even if your Python bot crashes, loses internet, or the `gluetun` VPN drops a millisecond after the order is sent, the exchange's matching engine already has the Stop Loss in its order book. Your downside is mathematically capped.

### 2. Immutable Double-Entry PnL Ledger

Do not rely on a single "current PnL" float variable. Implement a double-entry style ledger in PostgreSQL/Redis for every trade:

- `trade_id` (UUID)
- `symbol`, `side`, `entry_price`, `exit_price`
- `quantity`, `gross_pnl`
- `maker_fee`, `taker_fee`, `funding_fee`
- `net_pnl` (Gross - Fees - Funding)
- `timestamp_open`, `timestamp_close`

This allows you to audit exactly *why* a trade was profitable or not, separating strategy performance from fee drag.

### 3. Continuous PnL Reconciliation (The "Trust but Verify" Loop)

Local PnL calculations can drift from exchange PnL due to unaccounted funding rates, liquidation fees, or missed webhook fills.
**Implementation:** Run a background asyncio task every 60 seconds:

1. Fetch `account_info` and `position_list` from Bybit.
2. Calculate `exchange_unrealized_pnl` and `exchange_realized_pnl`.
3. Compare with `local_database_pnl`.
4. **Alerting**: If `abs(exchange_pnl - local_pnl) / exchange_pnl > 0.01` (1% divergence), trigger a **CRITICAL** alert (Telegram/Discord) and optionally pause trading until manual review.

### 4. Dynamic PnL Protection (Trailing & Breakeven)

Secure *unrealized* profits by programming the bot to manage the trade after entry:

- **Breakeven Trigger**: When `unrealized_pnl >= 1.5 * risk_amount`, automatically modify the Stop Loss to the `entry_price + fees`. The trade is now "risk-free".
- **Trailing Stop**: As price moves favorably, use the Bybit `replace_order` API to trail the Stop Loss behind the price by a fixed ATR (Average True Range) multiple.

---

## 🚀 Actionable Next Steps for Karsa

1. **Implement the `RiskEngine` Class**: Create a dedicated `app/risk/gate.py` module that enforces the 5-layer checklist *before* the SOR is called. Make it raise hard exceptions, not just log warnings.
2. **Refactor Bybit Client for Atomic Orders**: Update `app/execution/bybit_client.py` to mandate `stop_loss` and `take_profit` in the `create_order` payload. Remove the `_place_sl_after_fill` background task entirely—it is a liability.
3. **Build the PnL Reconciliation Cron**: Add a 60-second background task in `main.py` that compares local DB PnL with Bybit's reported PnL, logging any divergence > 0.5%.
4. **Add a "Hard Kill" Switch**: Ensure that if the Risk Gate detects a breach of the absolute max daily drawdown (e.g., 5%), it not only stops new orders but actively **market-closes all open positions** to prevent further bleed.

Would you like me to draft the complete code for the `RiskEngine` gate or the Atomic Bybit Order payload with integrated TP/SL?
