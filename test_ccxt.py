import asyncio
import ccxt.async_support as ccxt
import sys

async def test():
    session = ccxt.bybit({"options": {"defaultType": "swap"}})
    try:
        f = await session.fetch_funding_rate("BTC/USDT:USDT")
        print("Funding:", f)
    except Exception as e:
        print("Funding error:", type(e), e)
        
    try:
        oi = await session.fetch_open_interest("BTC/USDT:USDT")
        print("OI:", oi)
    except Exception as e:
        print("OI error:", type(e), e)
    
    await session.close()

asyncio.run(test())
