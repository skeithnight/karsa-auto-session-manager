import asyncio
import logging

from app.execution.bybit_client import BybitClient

logger = logging.getLogger(__name__)

async def main():
    client = BybitClient()
    await client.connect()

    positions = await client.fetch_positions()
    print(f"=== BYBIT OPEN POSITIONS ({len(positions)}) ===")
    for p in positions:
        print(f"Symbol: {p.get('symbol')} | Side: {p.get('side')} | Contracts: {p.get('contracts')} | Entry: {p.get('entry_price')} | PnL: {p.get('unrealized_pnl')} | SL: {p.get('stopLoss')}")

if __name__ == "__main__":
    asyncio.run(main())
