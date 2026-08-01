import asyncio

from app.execution.bybit_client import BybitClient


async def main():
    client = BybitClient()
    await client.connect()
    pos = await client.fetch_positions()
    for p in pos:
        print(p.get("symbol"), p.get("liquidationPrice"), p.get("info", {}).get("liqPrice"))
        # print full info
        import json
        print(json.dumps(p, indent=2))
        break

asyncio.run(main())
