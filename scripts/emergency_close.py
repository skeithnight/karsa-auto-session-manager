import asyncio
import logging
from decimal import Decimal

from app.execution.bybit_client import BybitClient

logger = logging.getLogger(__name__)

async def main():
    client = BybitClient()
    await client.connect()

    positions = await client.fetch_positions()
    print(f"=== FOUND {len(positions)} POSITIONS TO CLOSE ===")

    for p in positions:
        symbol = p.get("symbol")
        # Reverse the side to close
        side = "sell" if p.get("side") == "buy" else "buy"
        amount = Decimal(str(p.get("contracts")))

        print(f"Closing {symbol} | Side: {side} | Amount: {amount}")
        try:
            order = await client.create_market_order(symbol, side, amount, params={"reduceOnly": True})
            print(f"Successfully closed {symbol}: {order}")
        except Exception as e:
            print(f"Failed to close {symbol}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
