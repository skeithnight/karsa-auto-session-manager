import asyncio

from app.execution.bybit_client import BybitClient


async def main():
    c = BybitClient()
    await c.connect()
    tickers = await c.fetch_tickers()
    for t in tickers:
        if "ESPORT" in t.get("symbol", "") or "ZIL" in t.get("symbol", ""):
            print(t)
    await c.close()

asyncio.run(main())
