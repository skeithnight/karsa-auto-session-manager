import asyncio

from app.core.config import settings
from app.execution.bybit_client import BybitClient


async def main():
    client = BybitClient(
        api_key=settings.BYBIT_API_KEY,
        api_secret=settings.BYBIT_API_SECRET,
        testnet=settings.BYBIT_TESTNET
    )
    await client.connect()
    positions = await client.fetch_positions()
    for p in positions:
        print(f"Symbol: {p['symbol']}, Side: {p['side']}, Size: {p['contracts']}, Entry: {p['entry_price']}")

if __name__ == "__main__":
    asyncio.run(main())
