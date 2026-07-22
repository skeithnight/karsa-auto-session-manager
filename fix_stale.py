import asyncio
from app.core.config import get_settings
from app.core.redis_client import RedisClient
from app.core.database import DatabaseEngine
from sqlalchemy import text

async def main():
    settings = get_settings()
    
    # Fix 1: Redis stale shadow positions
    redis_client = RedisClient()
    await redis_client.connect()
    keys = await redis_client.redis.keys("shadow:position:*")
    count = 0
    for k in keys:
        if "ALLO" in k or "COIN" in k:
            await redis_client.redis.delete(k)
            count += 1
            print(f"Deleted stale shadow position: {k}")
    print(f"Total Redis keys deleted: {count}")
    await redis_client.disconnect()

    # Fix 2: Stale Trade 466
    db = DatabaseEngine()
    await db.connect(settings.postgres_url)
    async with db.engine.begin() as conn:
        query = text("""
            UPDATE trades 
            SET exit_time = NOW(), exit_price = entry_price, pnl = 0, exit_reason = 'manual_reconciliation_stale'
            WHERE id = 466 AND exit_time IS NULL
        """)
        res = await conn.execute(query)
        print(f"Updated {res.rowcount} stale trade(s) in DB (Trade 466).")
    await db.dispose()

if __name__ == "__main__":
    asyncio.run(main())
