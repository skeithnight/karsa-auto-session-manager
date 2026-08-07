# Fix Data Ingestion Stalling & Dynamic Symbol Validation

Your idea is excellent. Rather than manually curating the `settings.symbols` list and hoping they all match the exact string format for OKX, Binance, and Bybit, we can dynamically validate them at startup and store only the "good" ones.

This plan incorporates your idea along with the fix for the concurrent websocket loop.

## Proposed Changes

### 1. Dynamic Symbol Discovery (Your Idea)
We will modify the startup sequence to cross-reference our desired symbols against the exchanges.

#### [MODIFY] [ccxt_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/data/ccxt_manager.py)
- Update `CCXTManager.start()` to execute `await exchange.load_markets()` for Binance, OKX, and Bybit.
- Create a new method `get_valid_universe(target_symbols: list[str]) -> list[str]` that checks the loaded markets and returns only the symbols that exist on **all three** exchanges.

#### [MODIFY] [main.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py)
- Before starting the `data_engine_task`, the system will call `get_valid_universe(settings.symbols)`.
- The system will then save this validated list to the database (Redis).
- `data_engine_task` will load the symbols from the database array instead of the raw `.env` settings.

### 2. Concurrent Ingestion Loop
We still need to fix the `data_engine_task` freeze bug by consuming the valid symbols concurrently.

#### [MODIFY] [main.py](file:///Users/dwiki.nugraha/dwikicode/karsa-auto-session-manager/app/main.py)
- **Extract Consumer**: Move the inner body of the data engine loop into a separate async coroutine named `_stream_orderbook(symbol, exchange_id)`.
- **Concurrent Execution**: Instead of a sequential `for` loop, `data_engine_task` will use `asyncio.gather` to launch a dedicated background task for each `(symbol, exchange_id)` pair in the valid universe array.

## User Review Required
> [!IMPORTANT]
> **Data Model Addition**: You requested saving the valid symbols to the DB. According to strict Project Rule 1, I cannot invent a new database table or Redis key without adding it to `DATA_MODEL.md` first.
> I propose adding a new Redis key: `system:config:valid_symbols` (Type: JSON Array of strings) to store the validated list on startup. This fits perfectly with the fast state pattern in `DATA_MODEL.md`. Let me know if you approve this schema addition, or if you prefer a PostgreSQL table instead!

## Verification Plan

### Automated Tests
- Run `pytest` to ensure no syntax errors or regressions were introduced.

### Manual Verification
- Deploy the updated app using docker compose.
- Check the startup logs: it should log exactly how many symbols from the original 60 passed the 3-exchange validation.
- Verify Redis contains the `system:config:valid_symbols` key.
- Verify `karsa_heartbeat_age_seconds` in Grafana stays near zero and no symbols throw "market symbol not found" errors.
