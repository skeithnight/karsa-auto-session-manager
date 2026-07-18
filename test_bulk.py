import asyncio
import logging
from app.core.redis_client import RedisClient
from app.core.database import DatabaseEngine
from app.commander.main import scheduled_bulk_backtest_task

logging.basicConfig(level=logging.DEBUG)

async def main():
    redis_client = RedisClient()
    await redis_client.connect()
    
    db_engine = DatabaseEngine()
    await db_engine.connect("postgresql+asyncpg://karsa:karsa@postgres:5432/karsa")
    
    kill_switch = asyncio.Event()
    
    class FakeAlertService:
        async def send_alert(self, msg, **kwargs):
            print("SEND_ALERT:", msg)
            
    print("Running scheduled_bulk_backtest_task...")
    task = asyncio.create_task(scheduled_bulk_backtest_task(
        redis_client, db_engine, FakeAlertService(), kill_switch, interval_hours=24
    ))
    
    await asyncio.sleep(15) # Wait for it to finish fetching and submitting
    kill_switch.set()
    await task
    print("Done")

asyncio.run(main())
