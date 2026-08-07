## 🔴 Critical Finding: The "Zombie WebSocket" Problem

### 1. Missing Reconnection Loop in `ccxt_manager.py`

- **Location:** `app/data/ccxt_manager.py` (`watch_orderbook`, `watch_trades`)
- **Issue:** When a WebSocket disconnects or throws an error, the code correctly increments a metric and attempts to force-close the stale socket. However, it then simply **`raise`s the exception**:

  ```python
  except Exception as e:
      metrics.ws_disconnects.labels(exchange=exchange_id).inc()
      logger.error(f"WebSocket error on {exchange_id}: {e}")
      # ... force close logic ...
      raise  # <--- DANGER: Propagates crash to caller
  ```

- **Impact:** If the caller (e.g., a data consumer loop) does not have a robust `while True: try/except` wrapper with a reconnection delay, the WebSocket task will die permanently. The bot will continue running, but its market data will be completely frozen, leading to trades based on stale prices.
- **Fix:** The `CCXTManager` should encapsulate the reconnection logic internally, or the caller must implement a strict reconnection backoff.

### 2. Flawed Desync Detection in `system_watchdog.py`

- **Location:** `app/watchdog/system_watchdog.py` (`_check_position_desync`)
- **Issue:** The watchdog attempts to verify state integrity by comparing the *count* of positions:

  ```python
  redis_count = len(redis_positions) if redis_positions else 0
  bybit_count = len(bybit_positions) if bybit_positions else 0
  if redis_count != bybit_count:
      return f"position_desync: redis={redis_count} bybit={bybit_count}"
  ```

- **Impact:** This is a **useless heuristic**. The bot could have 1 position in Redis (e.g., `BTC/USDT LONG 0.5`) and 1 position on Bybit (e.g., `ETH/USDT SHORT 2.0`). The counts match (`1 == 1`), so the watchdog reports "clean," but the bot is completely desynchronized from reality.
- **Fix:** The watchdog must compare the **actual state content** (symbol, side, quantity), not just the array length.

---

## 🟡 Medium Severity: Concurrency & Error Handling

### 3. Silent Failure Masking in `market_data_ingestor.py`

- **Location:** `app/data/market_data_ingestor.py` (`_fetch_symbol`)
- **Issue:** The ingestor correctly uses `asyncio.gather(..., return_exceptions=True)`, which is an **excellent** pattern to prevent one symbol's API failure from crashing the whole cycle. However, it catches all exceptions and demotes them to `logger.debug`:

  ```python
  except Exception:
      logger.debug("MarketDataIngestor: orderbook fetch failed %s", symbol)
  ```

- **Impact:** If Bybit begins rate-limiting the bot (HTTP 429) or the API key loses permissions, the logs will be flooded with `debug` messages that are easily missed. The bot will silently stop receiving micro-structure data (funding, OI, orderbook delta), degrading the Alpha Bridge's scoring without triggering any alerts.
- **Fix:** Implement a "consecutive failure counter" per symbol. If failures exceed a threshold (e.g., 5), escalate to `logger.warning` and trigger a Telegram alert.

### 4. Lack of Explicit Concurrency Locks

- **Location:** `app/data/market_data_ingestor.py` (In-memory caches)
- **Issue:** The `MarketDataIngestor` updates shared dictionaries (`self.orderbook_delta`, `self.funding_rate`) asynchronously. While Python's GIL prevents true memory corruption on simple dict assignments, if the `update_consumer` method is called *while* `_fetch_symbol` is mid-update, the Alpha Bridge could read a partially updated state (e.g., new `orderbook_delta` but old `funding_rate` for the same symbol).
- **Fix:** Wrap the cache updates and reads in an `asyncio.Lock()` to guarantee atomic reads of the micro-structure snapshot.

---

## 🟢 Strengths in Resilience

1. **Dead Man’s Switch (DMS):** The `dead_mans_switch.py` implementation is robust. It uses `aiohttp` with a strict 5-second timeout and a 3-attempt exponential backoff loop. If the bot hangs, the external service (e.g., Healthchecks.io) will correctly detect the absence of pings and alert you.
2. **REST Polling Fallback:** The decision to use REST polling (`fetch_order_book`, `fetch_open_interest`) every 30 seconds in `market_data_ingestor.py` instead of WebSockets for micro-structure data is **highly resilient**. REST requests are stateless and much easier to retry than managing persistent WebSocket subscriptions.
3. **Global Halt Mechanism:** The `SystemWatchdog` correctly writes to a centralized Redis key (`karsa:global_halt`). This is the correct "fail-closed" pattern, ensuring that if *any* component detects a critical issue, the entire system halts, not just the faulty module.

---

## 🛠 Actionable Code Fixes

### Fix 1: Correct the Watchdog Position Desync Logic

Replace the flawed count comparison in `system_watchdog.py` with a content-based reconciliation:

```python
async def _check_position_desync(self) -> str | None:
    """Compare actual position state (symbol, side, qty) between Redis and Bybit."""
    try:
        redis_positions = await self._positions.list_all()
        bybit_positions = await self._bybit.fetch_positions()
        
        # Create a normalized set of active positions: "SYMBOL:SIDE:QTY"
        redis_state = {
            f"{p['symbol']}:{p['side']}:{p['qty']}" 
            for p in (redis_positions or []) if float(p.get('qty', 0)) != 0.0
        }
        bybit_state = {
            f"{p['symbol']}:{p['side']}:{p['contracts']}" # Adjust 'contracts' to match your Bybit schema
            for p in (bybit_positions or []) if float(p.get('contracts', 0)) != 0.0
        }
        
        if redis_state != bybit_state:
            return f"position_content_desync: redis={redis_state} | bybit={bybit_state}"
        return None
    except Exception as e:
        logger.debug(f"SystemWatchdog: position desync check failed: {e}")
        return None
```

### Fix 2: Add Consecutive Failure Escalation to Data Ingestor

Prevent silent API degradation in `market_data_ingestor.py`:

```python
# Add to __init__:
self._failure_counts: dict[str, int] = {s: 0 for s in symbols}
self._max_debug_failures = 3

# Update _fetch_symbol:
async def _fetch_symbol(self, symbol: str) -> None:
    # ... existing setup ...
    try:
        await self._fetch_orderbook(symbol, ccxt_sym)
        self._failure_counts[symbol] = 0 # Reset on success
    except Exception as e:
        self._failure_counts[symbol] += 1
        if self._failure_counts[symbol] > self._max_debug_failures:
            logger.warning(f"MarketDataIngestor: Persistent orderbook fetch failure for {symbol} ({self._failure_counts[symbol]} times). Last error: {e}")
            # Optional: Trigger alert here if count > 10
        else:
            logger.debug(f"MarketDataIngestor: orderbook fetch failed {symbol}")
```

### Fix 3: Enforce Atomic Reconnection in CCXT Manager

If you are using `watch_orderbook` anywhere, wrap the caller in a resilient loop, or modify `ccxt_manager.py` to handle it:

```python
async def watch_orderbook_resilient(self, symbol: str, exchange_id: str) -> dict:
    """Wrapper that automatically reconnects on WebSocket failure."""
    while True:
        try:
            return await self.watch_orderbook(symbol, exchange_id)
        except Exception as e:
            logger.warning(f"WebSocket for {symbol} on {exchange_id} died: {e}. Reconnecting in 3s...")
            metrics.ws_reconnects.labels(exchange=exchange_id).inc()
            await asyncio.sleep(3) # Backoff before retry
```

---
