import asyncio
from app.execution.bybit_client import BybitClient
from app.core.config import settings

async def main():
    client = BybitClient(api_key=settings.BYBIT_API_KEY, api_secret=settings.BYBIT_API_SECRET, testnet=settings.USE_TESTNET)
    await client.initialize()
    print("ROAM tick:", client._price_ticks.get("ROAM/USDT"))

asyncio.run(main())
